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

# --- Constants ---
GRADE_POINTS_MAP = {'O': 10, 'A+': 9, 'A': 8, 'B+': 7, 'B': 6, 'C': 5, 'U': 0}

# --- Grading Logic ---

def apply_fixed_grading(marks):
    """
    Apply fixed grading scheme based on absolute marks for <= 30 students.
    """
    def get_grade(mark):
        if pd.isna(mark) or mark < 50: return 'U'
        if mark >= 91: return 'O'
        if mark >= 81: return 'A+'
        if mark >= 71: return 'A'
        if mark >= 61: return 'B+'
        if mark >= 56: return 'B'
        if mark >= 50: return 'C'
        return 'U'

    grades = marks.apply(get_grade)
    valid_marks = marks.dropna()
    normalized_values = pd.Series(np.nan, index=marks.index)

    if not valid_marks.empty:
        min_mark, max_mark = valid_marks.min(), valid_marks.max()
        mark_range = max_mark - min_mark
        if mark_range > 0:
            normalized_values.update((valid_marks - min_mark) / mark_range)
        else:
            normalized_values.update(pd.Series(1.0, index=valid_marks.index))

    return grades, normalized_values

def apply_relative_grading(marks):
    """
    Apply relative grading for > 30 students to achieve a bell-curve distribution.
    This is applied ONLY to students who have passed (marks >= 50).
    """
    final_grades = pd.Series('U', index=marks.index)
    final_normalized_values = pd.Series(np.nan, index=marks.index)
    passed_mask = (marks >= 50) & marks.notna()
    passed_marks = marks[passed_mask]

    if passed_marks.empty:
        return final_grades, final_normalized_values

    if passed_marks.nunique() < 2:
        passed_grades, passed_norm_vals = apply_fixed_grading(passed_marks)
        final_grades.update(passed_grades)
        final_normalized_values.update(passed_norm_vals)
        return final_grades, final_normalized_values

    try:
        transformed_marks, _ = boxcox(passed_marks)
        transformed_series = pd.Series(transformed_marks, index=passed_marks.index)
        final_normalized_values.update(transformed_series)

        mean = transformed_series.mean()
        std_dev = transformed_series.std()
        
        if std_dev == 0:
            raise ValueError("Standard deviation is zero, cannot apply relative grading.")

        # --- Define Grade Cutoffs based on Standard Deviations from the Mean ---
        o_cutoff      = mean + 1.65 * std_dev
        a_plus_cutoff = mean + 0.85 * std_dev
        a_cutoff      = mean
        b_plus_cutoff = mean - 0.9 * std_dev
        b_cutoff      = mean - 1.8 * std_dev

        # --- CORRECTED: Assign Grades using explicit ranges for a proper bell curve ---
        # This uses np.select for clear, non-overlapping conditions.
        conditions = [
            (transformed_series >= o_cutoff),
            (transformed_series >= a_plus_cutoff),
            (transformed_series >= a_cutoff),
            (transformed_series >= b_plus_cutoff),
            (transformed_series >= b_cutoff),
        ]
        grade_choices = ['O', 'A+', 'A', 'B+', 'B', 'C']
        
        # default='C' handles passed students below the c_cutoff
        passed_grades = pd.Series(np.select(conditions, grade_choices, default='C'), index=passed_marks.index)

        final_grades.update(passed_grades)

    except Exception as e:
        print(f"Relative grading failed: {e}. Falling back to fixed grading.")
        passed_grades, passed_norm_vals = apply_fixed_grading(passed_marks)
        final_grades.update(passed_grades)
        final_normalized_values.update(passed_norm_vals)

    return final_grades, final_normalized_values

def calculate_grade_ranges(df):
    """Calculates the min-max mark range for each grade present in the DataFrame."""
    grade_ranges = {}
    ranges_df = df.groupby('Grade')['Marks'].agg(['min', 'max']).dropna()
    for grade, stats in ranges_df.iterrows():
        min_mark, max_mark = int(stats['min']), int(stats['max'])
        grade_ranges[grade] = f"{min_mark} - {max_mark}" if min_mark != max_mark else str(min_mark)
    return grade_ranges

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
            return jsonify({'error': 'Excel file must contain a name column (e.g., "Name")'}), 400
        
        df.rename(columns={name_col_found: 'Name'}, inplace=True)
        df['Marks'] = pd.to_numeric(df['Marks'], errors='coerce')
        
        valid_students_count = len(df.dropna(subset=['Marks']))
        grading_method = 'relative_grading' if valid_students_count > 30 else 'fixed_grading'
        grading_function = apply_relative_grading if grading_method == 'relative_grading' else apply_fixed_grading
        
        df['Grade'], df['Normalized_Value'] = grading_function(df['Marks'])
        df['Grade_Points'] = df['Grade'].map(GRADE_POINTS_MAP).fillna(0).astype(int)

        valid_marks = df['Marks'].dropna()
        summary_stats = {
            'count': int(valid_marks.count()),
            'average': round(valid_marks.mean(), 2) if not valid_marks.empty else 0,
            'max': int(valid_marks.max()) if not valid_marks.empty else 0,
            'min': int(valid_marks.min()) if not valid_marks.empty else 0,
            'grading_method': grading_method,
            'grade_ranges': calculate_grade_ranges(df)
        }
        
        display_cols = ['Name', 'Marks', 'Grade', 'Grade_Points', 'Normalized_Value']
        df_display = df[display_cols].copy()
        df_display['Marks'] = df_display['Marks'].astype(object).where(df_display['Marks'].notna(), None)
        df_display['Normalized_Value'] = df_display['Normalized_Value'].astype(object).where(df_display['Normalized_Value'].notna(), None)
        student_details = df_display.to_dict(orient='records')
        
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

# --- Error Handlers ---
@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large. Maximum size is 16MB.'}), 413

if __name__ == '__main__':
    print("Starting Student Grading System API...")
    app.run(debug=True, host='0.0.0.0', port=5000)
