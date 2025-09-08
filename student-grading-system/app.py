from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pandas as pd
import numpy as np
from scipy.stats import boxcox
import io
from werkzeug.utils import secure_filename
import os
import uuid

app = Flask(__name__)
CORS(app) 

# --- Configuration ---
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# --- Grading Logic ---

def apply_fixed_grading(marks):
    """
    Apply fixed grading scheme based on absolute marks.
    This is used when student count is <= 30.
    Returns a tuple of two pandas Series: (grades, normalized_values).
    """
    # --- Grade Calculation based on provided TABLE-8 ---
    def get_grade(mark):
        if pd.isna(mark) or mark < 50:
            return 'U'
        if mark >= 91:
            return 'O'
        elif mark >= 81:
            return 'A+'
        elif mark >= 71:
            return 'A'
        elif mark >= 61:
            return 'B+'
        elif mark >= 56:
            return 'B'
        elif mark >= 50:
            return 'C'
        else:
            return 'U'
    
    grades = marks.apply(get_grade)

    # --- Normalization (Min-Max) ---
    # Normalization is applied to all marks for statistical purposes
    valid_marks = marks.dropna()
    normalized_values = pd.Series(np.nan, index=marks.index)
    
    if not valid_marks.empty:
        min_mark = valid_marks.min()
        max_mark = valid_marks.max()
        mark_range = max_mark - min_mark
        
        if mark_range > 0:
            normalized_values.update((valid_marks - min_mark) / mark_range)
        else: # Handle case where all marks are identical
            normalized_values.update(pd.Series(1.0, index=valid_marks.index))
            
    return grades, normalized_values

def apply_relative_grading(marks):
    """
    Apply relative grading using Box-Cox transformation for >30 students.
    Crucially, this is ONLY applied to students who have passed (marks >= 50).
    Returns a tuple of two pandas Series: (grades, normalized_values).
    """
    # Initialize Series to hold the final results for all students
    final_grades = pd.Series('U', index=marks.index)
    final_normalized_values = pd.Series(np.nan, index=marks.index)

    # --- Isolate Passed Students ---
    # Relative grading is only for students with marks >= 50
    passed_mask = marks >= 50
    passed_marks = marks[passed_mask]

    # If no one passed, everyone gets 'U', so we can return early
    if passed_marks.empty:
        return final_grades, final_normalized_values

    # --- Fallback for small number of passed students ---
    # If fewer than 2 students passed, Box-Cox is not viable.
    # Use fixed grading for those who passed.
    if len(passed_marks) < 2:
        passed_grades, passed_norm_vals = apply_fixed_grading(passed_marks)
        final_grades.update(passed_grades)
        final_normalized_values.update(passed_norm_vals)
        return final_grades, final_normalized_values

    # Ensure all values are positive for Box-Cox
    adjusted_marks = passed_marks
    # min_passed_mark = passed_marks.min()
    # if min_passed_mark <= 0:
    #     # Add 1 to ensure all values are > 0
    #     adjusted_marks = passed_marks + abs(min_passed_mark) + 1

    # Fallback if all passed marks are the same
    if adjusted_marks.nunique() == 1:
        passed_grades, passed_norm_vals = apply_fixed_grading(passed_marks)
        final_grades.update(passed_grades)
        final_normalized_values.update(passed_norm_vals)
        return final_grades, final_normalized_values

    try:
        # --- Apply Box-Cox Transformation on Passed Students ---
        transformed_marks, _ = boxcox(adjusted_marks)
        
        # Store transformed marks in the final normalized values series
        final_normalized_values.loc[passed_mask] = transformed_marks
        
        # Calculate percentiles for grade boundaries from the transformed marks
        percentiles = np.percentile(transformed_marks, [85, 70, 55, 40, 25, 10])
        o_cutoff, a_plus_cutoff, a_cutoff, b_plus_cutoff, b_cutoff, c_cutoff = percentiles
        
        # --- Assign Grades to Passed Students ---
        # Create a temporary series for grades of passed students
        passed_grades = pd.Series('C', index=passed_marks.index) # Default passed grade is C
        
        # Vectorially assign grades based on transformed value cutoffs
        passed_grades[final_normalized_values.loc[passed_mask] >= b_cutoff] = 'B'
        passed_grades[final_normalized_values.loc[passed_mask] >= b_plus_cutoff] = 'B+'
        passed_grades[final_normalized_values.loc[passed_mask] >= a_cutoff] = 'A'
        passed_grades[final_normalized_values.loc[passed_mask] >= a_plus_cutoff] = 'A+'
        passed_grades[final_normalized_values.loc[passed_mask] >= o_cutoff] = 'O'
        
        # Update the final grades series with the calculated relative grades
        final_grades.update(passed_grades)
        
        return final_grades, final_normalized_values
    
    except Exception as e:
        print(f"Box-Cox transformation failed: {e}. Falling back to fixed grading for passed students.")
        passed_grades, passed_norm_vals = apply_fixed_grading(passed_marks)
        final_grades.update(passed_grades)
        final_normalized_values.update(passed_norm_vals)
        return final_grades, final_normalized_values

# --- API Endpoints ---

processed_files = {}

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part in the request'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected for uploading'}), 400
        
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            return jsonify({'error': 'Invalid file format. Please upload an Excel file.'}), 400
        
        df = pd.read_excel(file)
        
        if 'Marks' not in df.columns:
            return jsonify({'error': 'Excel file must contain a "Marks" column'}), 400
        
        name_col_found = next((col for col in ['Name', 'Student Name', 'Student', 'name'] if col in df.columns), None)
        if not name_col_found:
            return jsonify({'error': 'Excel file must contain a column for student names (e.g., "Name")'}), 400
        
        df.rename(columns={name_col_found: 'Name'}, inplace=True)
        df['Marks'] = pd.to_numeric(df['Marks'], errors='coerce')
        
        # Determine which grading method to use
        grading_method = 'relative_grading' if len(df.dropna(subset=['Marks'])) > 30 else 'fixed_grading'
        grading_function = apply_relative_grading if grading_method == 'relative_grading' else apply_fixed_grading
        
        # Apply grading function
        df['Grade'], df['Normalized_Value'] = grading_function(df['Marks'])
        
        #  **MODIFICATION HERE**: Calculate Grade Points based on the assigned Grade
        grade_points_map = {'O': 10, 'A+': 9, 'A': 8, 'B+': 7, 'B': 6, 'C': 5, 'U': 0}
        df['Grade_Points'] = df['Grade'].map(grade_points_map).fillna(0).astype(int)

        # --- Prepare Response Data ---
        valid_marks = df['Marks'].dropna()
        summary_stats = {
            'count': int(valid_marks.count()),
            'average': round(valid_marks.mean(), 2) if not valid_marks.empty else 0,
            'max': int(valid_marks.max()) if not valid_marks.empty else 0,
            'min': int(valid_marks.min()) if not valid_marks.empty else 0,
            'grading_method': grading_method
        }
        
        # Select and format columns for the JSON response and Excel output
        #  **MODIFICATION HERE**: Added 'Grade_Points'
        display_cols = ['Name', 'Marks', 'Grade', 'Grade_Points', 'Normalized_Value']
        df_display = df[display_cols].copy()
        
        # Convert NaN/NaT to None for proper JSON serialization
        for col in ['Marks', 'Normalized_Value']:
            df_display[col] = df_display[col].astype(object).where(df_display[col].notna(), None)
            
        student_details = df_display.to_dict(orient='records')
        
        # --- Store Processed File for Download ---
        output = io.BytesIO()
        df.to_excel(output, index=False, sheet_name='Graded_Results', engine='openpyxl')
        output.seek(0)
        
        original_filename = secure_filename(file.filename)
        output_filename = f"{os.path.splitext(original_filename)[0]}_graded.xlsx"
        
        file_id = str(uuid.uuid4())
        processed_files[file_id] = {'data': output.getvalue(), 'filename': output_filename}
        
        return jsonify({
            'message': 'File processed successfully',
            'file_id': file_id,
            'filename': output_filename,
            'summary': summary_stats,
            'details': student_details
        }), 200
    
    except Exception as e:
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

@app.route('/download/<file_id>', methods=['GET'])
def download_specific_file(file_id):
    try:
        if file_id not in processed_files:
            return jsonify({'error': 'File not found or has expired'}), 404
        
        file_data = processed_files[file_id]
        
        # Use a new BytesIO object to not interfere with the stored data
        buffer = io.BytesIO(file_data['data'])
        buffer.seek(0)
        
        return send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=file_data['filename']
        )
    
    except Exception as e:
        return jsonify({'error': f'Download failed: {str(e)}'}), 500

# --- Health and Info Endpoints ---
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'message': 'Student Grading System API is running'})

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'message': 'Student Grading System API',
        'version': '1.2.0',
        'endpoints': {
            'POST /upload': 'Upload Excel file for grading.',
            'GET /download/<file_id>': 'Download the processed Excel file.',
            'GET /health': 'Health check endpoint.'
        }
    })

# --- Error Handlers ---
@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large. Maximum size is 16MB.'}), 413

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'error': 'Method not allowed'}), 405

if __name__ == '__main__':
    print("Starting Student Grading System API...")
    app.run(debug=True, host='0.0.0.0', port=5000)
