from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pandas as pd
import numpy as np
import io
from werkzeug.utils import secure_filename
import os
import uuid
import json
from datetime import datetime

# --- PDF Imports ---
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT 
# -------------------

app = Flask(__name__)
CORS(app)

# --- Configuration ---
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# --- Constants ---
GRADE_POINTS_MAP = {'O': 10, 'A+': 9, 'A': 8, 'B+': 7, 'B': 6, 'C': 5, 'U': 0}

# --- Global State ---
processed_files = {}
grade_cutoffs = None

# --- Grading Logic (Unmodified) ---
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
    return grades

def apply_relative_grading(marks):
    """
    Apply relative grading for > 30 students to achieve a bell-curve distribution.
    This is applied ONLY to students who have passed (marks >= 50).
    """
    global grade_cutoffs
    
    final_grades = pd.Series('U', index=marks.index)
    
    passed_mask = (marks >= 50) & marks.notna()
    passed_marks = marks[passed_mask]
    
    if passed_marks.empty:
        grade_cutoffs = None
        return final_grades
    
    if passed_marks.nunique() < 2:
        passed_grades = apply_fixed_grading(passed_marks)
        final_grades.update(passed_grades)
        grade_cutoffs = None
        return final_grades
    
    try:
        # --- Use raw marks directly for relative grading ---
        mean = passed_marks.mean()
        std_dev = passed_marks.std()

        if std_dev == 0:
            raise ValueError("Standard deviation is zero, cannot apply relative grading.")

        # --- Define Grade Cutoffs based on Standard Deviations from the Mean ---
        o_cutoff      = mean + 1.65 * std_dev
        a_plus_cutoff = mean + 0.85 * std_dev
        a_cutoff      = mean
        b_plus_cutoff = mean - 0.9 * std_dev
        b_cutoff      = mean - 1.8 * std_dev

        # Store cutoffs globally for grade range calculation
        grade_cutoffs = {
            'o_cutoff': o_cutoff,
            'a_plus_cutoff': a_plus_cutoff,
            'a_cutoff': a_cutoff,
            'b_plus_cutoff': b_plus_cutoff,
            'b_cutoff': b_cutoff
        }

        # Assign Grades using explicit ranges for a proper bell curve
        conditions = [
            (passed_marks >= o_cutoff),
            (passed_marks >= a_plus_cutoff),
            (passed_marks >= a_cutoff),
            (passed_marks >= b_plus_cutoff),
            (passed_marks >= b_cutoff),
        ]
        grade_choices = ['O', 'A+', 'A', 'B+', 'B']

        passed_grades = pd.Series(np.select(conditions, grade_choices, default='C'), index=passed_marks.index)
        final_grades.update(passed_grades)

    except Exception as e:
        print(f"Relative grading failed: {e}. Falling back to fixed grading.")
        passed_grades = apply_fixed_grading(passed_marks)
        final_grades.update(passed_grades)
        grade_cutoffs = None

    return final_grades

def calculate_continuous_grade_ranges(df, grading_method):
    """
    Calculates continuous mark ranges for each grade based on cutoffs, which are
    used for documentation in the output Excel sheet and PDF.
    """
    global grade_cutoffs
    grade_ranges = {}
    
    if grading_method == 'relative_grading' and grade_cutoffs is not None:
        cutoffs = grade_cutoffs
        
        # Round cutoffs to integers (use round for standard cutoff calculation)
        o_min = int(round(cutoffs['o_cutoff']))
        ap_min = int(round(cutoffs['a_plus_cutoff']))
        a_min = int(round(cutoffs['a_cutoff']))
        bp_min = int(round(cutoffs['b_plus_cutoff']))
        b_min = int(round(cutoffs['b_cutoff']))

        grade_ranges['O'] = f"{max(o_min, 50)} - 100" 
        grade_ranges['A+'] = f"{max(ap_min, 50)} - {max(o_min - 1, 49)}"
        grade_ranges['A'] = f"{max(a_min, 50)} - {max(ap_min - 1, 49)}"
        grade_ranges['B+'] = f"{max(bp_min, 50)} - {max(a_min - 1, 49)}"
        grade_ranges['B'] = f"{max(b_min, 50)} - {max(bp_min - 1, 49)}"
        grade_ranges['C'] = f"50 - {max(b_min - 1, 49)}"
        grade_ranges['U'] = "Below 50"
        
        # Final adjustment to ensure strict ordering and min pass mark of 50
        grades_ordered = ['O', 'A+', 'A', 'B+', 'B', 'C']
        
        for i, grade in enumerate(grades_ordered):
            try:
                current_lower = int(grade_ranges[grade].split(' - ')[0])
                
                if i > 0:
                    prev_grade = grades_ordered[i-1]
                    prev_lower = int(grade_ranges[prev_grade].split(' - ')[0])
                    current_upper = max(prev_lower - 1, 49)
                else:
                    current_upper = 100
                    
                current_lower = max(current_lower, 50)
                
                grade_ranges[grade] = f"{min(current_lower, current_upper)} - {current_upper}"
                
                if grade == 'C':
                    grade_ranges['C'] = f"50 - {current_upper}"

            except Exception:
                 pass
                 
    else:
        # Use fixed grading ranges
        grade_ranges['O'] = "91 - 100"
        grade_ranges['A+'] = "81 - 90"
        grade_ranges['A'] = "71 - 80"
        grade_ranges['B+'] = "61 - 70"
        grade_ranges['B'] = "56 - 60"
        grade_ranges['C'] = "50 - 55"
        grade_ranges['U'] = "Below 50"
    
    return grade_ranges

# --- PDF Generation Logic (MODIFIED for column removal and layout) ---
def generate_pdf_from_data(df_export, summary_stats, academic_details, grade_point_map):
    """
    Generates a PDF byte stream from the processed DataFrame and summary data,
    with improved alignment, spacing, and a flexible signature block.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            leftMargin=0.5*inch, rightMargin=0.5*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    Story = []
    
    # --- Styles and Date ---
    h1 = styles['Heading1']
    h1.fontSize = 14
    h1.alignment = TA_CENTER
    h2 = styles['Heading2']
    h2.fontSize = 10
    h2.alignment = TA_CENTER
    
    # Custom styles
    bold_left = ParagraphStyle('BoldLeft', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, alignment=TA_LEFT)
    normal_left = ParagraphStyle('NormalLeft', parent=styles['Normal'], fontName='Helvetica', fontSize=10, alignment=TA_LEFT)
    normal_right = ParagraphStyle('NormalRight', parent=styles['Normal'], fontName='Helvetica', fontSize=10, alignment=TA_RIGHT)
    sig_style = ParagraphStyle('Signature', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, alignment=TA_CENTER)

    # Current Date
    current_date = datetime.now().strftime("%d-%b-%Y")
    # -----------------------

    # --- 1. Institutional Header ---
    Story.append(Paragraph('NATIONAL ENGINEERING COLLEGE, K.R. NAGAR, KOVILPATTI – 628 503', h1))
    Story.append(Paragraph('(An Autonomous Institution Affiliated to Anna University, Chennai)', h2))
    Story.append(Paragraph('NPTEL - Grade Fixing', h2))
    Story.append(Spacer(1, 0.2 * inch))

    # --- 2. Course Details (Redesigned 5-Column Table) ---
    total_width = doc.width
    
    details_data = [
        [
            Paragraph("Academic Year", bold_left), ':', Paragraph(academic_details['academic_year'], normal_left), 
            Paragraph("Date", bold_left), Paragraph(current_date, normal_right)
        ],
        [
            Paragraph("Subject Code", bold_left), ':', Paragraph(academic_details['subject_code'], normal_left), 
            '', ''
        ],
        [
            Paragraph("Subject Name", bold_left), ':', Paragraph(academic_details['subject_name'], normal_left), 
            '', ''
        ],
        [
            Paragraph("Total Number of Students", bold_left), ':', Paragraph(str(academic_details['expected_total_students']), normal_left), 
            '', ''
        ]
    ]

    # Width distribution: 25% (Label), 1% (Colon), 38% (Value), 10% (Date Label), 26% (Date Value - reduced gap)
    detail_col_widths = [total_width * 0.25, total_width * 0.01, total_width * 0.38, total_width * 0.10, total_width * 0.26]
    detail_table = Table(details_data, colWidths=detail_col_widths)

    detail_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'), 
        ('ALIGN', (4, 0), (4, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('SPAN', (3, 1), (4, 1)),
        ('SPAN', (3, 2), (4, 2)),
        ('SPAN', (3, 3), (4, 3)),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    
    Story.append(detail_table)
    Story.append(Spacer(1, 0.2 * inch))
    
    # --- 3. Student Results Table (MODIFIED: Remove Grade_Points column) ---
    Story.append(Paragraph('Student Grading Results', h2))
    Story.append(Spacer(1, 0.1 * inch))

    pdf_df = df_export.copy()
    
    # Filter out Grade_Points column
    if 'Grade_Points' in pdf_df.columns:
        pdf_df = pdf_df.drop(columns=['Grade_Points'])
        
    data = [pdf_df.columns.tolist()] 
    for _, row in pdf_df.iterrows():
        new_row = []
        for item in row:
            if pd.isna(item):
                new_row.append('')
            elif isinstance(item, (int, float)):
                 new_row.append(int(item) if item == int(item) else round(item, 2))
            else:
                 new_row.append(str(item))
        data.append(new_row)
    
    # Recalculate column widths based on the reduced number of columns
    total_cols = len(data[0])
    
    # Define widths for standard columns: Name, Marks, Grade (Total 50%)
    # Assuming original columns were: Name, Marks, Grade, Grade_Points, [Subject, etc.]
    # New Standard: Name(25), Marks(15), Grade(10) = 50%
    standard_cols = ['Name', 'Marks', 'Grade']
    
    # Calculate widths for the remaining columns dynamically
    remaining_cols = total_cols - len(standard_cols)
    remaining_width_percent = 100 - 50 # 50% left for other columns
    
    percentage_widths = [25, 15, 10]
    
    if remaining_cols > 0:
        width_per_remaining_col = remaining_width_percent / remaining_cols
        percentage_widths = percentage_widths + ([width_per_remaining_col] * remaining_cols)
    
    col_widths = [doc.width * (w / 100) for w in percentage_widths]
    
    table = Table(data, colWidths=col_widths)
    
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003366')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
    ]))
    
    Story.append(table)
    Story.append(Spacer(1, 0.2 * inch))
    
    # --- 4. Grading Summary Table (MODIFIED: Grade Point removed, Title bold) ---
    
    summary_flowables = []
    summary_flowables.append(Paragraph(f'Grading Summary ({summary_stats["grading_method"].replace("_", " ").title()})', bold_left))
    summary_flowables.append(Spacer(1, 0.1 * inch))

    summary_data = [
        [Paragraph('<b>Grade</b>', styles['Normal']), Paragraph('<b>Mark Range</b>', styles['Normal'])]
    ]
    # Sort by grade points
    sorted_grades = sorted(summary_stats['grade_ranges'].keys(), key=lambda x: grade_point_map.get(x, -1), reverse=True)
    for grade in sorted_grades:
        summary_data.append([grade, summary_stats['grade_ranges'][grade]])
        
    # Set summary table to use a fixed portion of the page width
    summary_table_width = doc.width * 0.5
    summary_col_widths = [summary_table_width/2] * 2
    summary_table = Table(summary_data, colWidths=summary_col_widths)
    
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6699CC')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#E0E0E0')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
    ]))
    
    summary_flowables.append(summary_table)

    # Use KeepTogether to ensure the Summary section stays together
    Story.append(KeepTogether(summary_flowables))
    
    # --- 5. Signatories Block (Flexible positioning) ---
    
    # Create the signature table data
    signatures = [
        ['', '', '', ''], # Spacer row for signature lines
        [
            Paragraph("Generated By", sig_style), 
            Paragraph("Verified By", sig_style), 
            Paragraph("Dean Academic", sig_style), 
            Paragraph("Principal", sig_style)
        ],
    ]

    sig_col_widths = [doc.width/4] * 4
    # The signature lines need a gap below the content and a height for the text
    sig_table = Table(signatures, colWidths=sig_col_widths, rowHeights=[0.5*inch, 0.2*inch])

    sig_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 1), (-1, 1), 0),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 0),
    ]))
    
    # The signature block is added as the last element. ReportLab will automatically
    # push it to the next page if there is not enough space.
    Story.append(Spacer(1, 0.5 * inch)) 
    Story.append(sig_table)


    doc.build(Story)
    buffer.seek(0)
    return buffer.getvalue()


# --- API Endpoints (Unmodified) ---

@app.route('/upload', methods=['POST'])
def upload_file():
    global grade_cutoffs
    
    try:
        # Require expected values before processing
        expected_total = request.form.get('expected_total_students')
        expected_subject = request.form.get('subject_code')
        
        # --- Extracting academic details ---
        academic_year = request.form.get('academic_year', 'N/A')
        subject_name = request.form.get('subject_name', 'N/A')
        # ---------------------------------------

        if expected_total is None or expected_subject is None:
            return jsonify({'error': 'Please provide expected_total_students and subject_code in the form data before uploading.'}), 400

        # Validate expected_total is an integer
        try:
            expected_total = int(str(expected_total).strip())
            if expected_total < 0:
                raise ValueError()
        except Exception:
            return jsonify({'error': 'expected_total_students must be a non-negative integer'}), 400

        expected_subject = str(expected_subject).strip()

        if 'file' not in request.files:
            return jsonify({'error': 'No file part in the request'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'No file selected for uploading'}), 400

        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            return jsonify({'error': 'Invalid file format. Please upload an Excel file.'}), 400

        df = pd.read_excel(file)

        # Basic required columns
        if 'Marks' not in df.columns:
            return jsonify({'error': 'Excel file must contain a "Marks" column'}), 400

        name_col_found = next((col for col in ['Name', 'Student Name', 'Student', 'name'] if col in df.columns), None)
        if not name_col_found:
            return jsonify({'error': 'Excel file must contain a name column (e.g., "Name")'}), 400

        df.rename(columns={name_col_found: 'Name'}, inplace=True)
        df['Marks'] = pd.to_numeric(df['Marks'], errors='coerce')

        # Attempt to find a subject column (case-insensitive 'subject' in the column name)
        subject_col_found = next((col for col in df.columns if 'subject' in col.lower()), None)
        if subject_col_found is None:
            print("Warning: Subject column not found in uploaded file. Skipping subject verification.")
        else:
            # Extract unique subject codes from the sheet (non-empty)
            unique_subjects = df[subject_col_found].dropna().astype(str).str.strip().unique()
            if len(unique_subjects) == 0:
                print("Warning: No subject code value found for verification. Skipping.")
            elif len(unique_subjects) > 1:
                found_subjects = unique_subjects.tolist()
                return jsonify({
                    'error': 'Multiple different subject codes found in the sheet. Please ensure the sheet contains a single subject.',
                    'found_subjects': found_subjects
                }), 400
            
            sheet_subject = unique_subjects[0] if len(unique_subjects) == 1 else None
            if sheet_subject and sheet_subject.lower() != expected_subject.lower():
                 return jsonify({'error': f'Subject code mismatch: expected "{expected_subject}", found "{sheet_subject}". Please correct and retry.'}), 400


        # Verify student count
        file_student_count = int(df['Name'].dropna().shape[0])
        if file_student_count != expected_total:
            return jsonify({'error': f'Student count mismatch: expected {expected_total}, found {file_student_count}. Please correct and retry.'}), 400

        # Proceed with existing grading logic now that verification passed
        valid_students_count = len(df.dropna(subset=['Marks']))
        grading_method = 'relative_grading' if valid_students_count > 30 else 'fixed_grading'
        grading_function = apply_relative_grading if grading_method == 'relative_grading' else apply_fixed_grading

        # Apply grading (functions return a Series of grade letters)
        df['Grade'] = grading_function(df['Marks'])
        df['Grade_Points'] = df['Grade'].map(GRADE_POINTS_MAP).fillna(0).astype(int)
        
        # --- Generate Grading Summary ---
        continuous_ranges = calculate_continuous_grade_ranges(df, grading_method)
        
        # Summary statistics use raw marks
        valid_marks = df['Marks'].dropna()
        summary_stats = {
            'count': int(valid_marks.count()),
            'average': round(valid_marks.mean(), 2) if not valid_marks.empty else 0,
            'max': int(valid_marks.max()) if not valid_marks.empty else 0,
            'min': int(valid_marks.min()) if not valid_marks.empty else 0,
            'grading_method': grading_method,
            'grade_ranges': continuous_ranges 
        }

        # --- Student Details for Frontend Display (keep Grade_Points here) ---
        display_cols = ['Name', 'Marks', 'Grade', 'Grade_Points']
        df_display = df[display_cols].copy()
        df_display['Marks'] = df_display['Marks'].astype(object).where(df_display['Marks'].notna(), None)
        student_details = df_display.to_dict(orient='records')
        # ---------------------------------------------------------------------

        # Remove Normalized_Value column if present before writing output Excel
        df_export = df.copy()
        if 'Normalized_Value' in df_export.columns:
            df_export = df_export.drop(columns=['Normalized_Value'])

        student_cols = df_export.columns.tolist()
        num_student_cols = len(student_cols)
        
        # --- 1. Create Header Rows (Institutional Header, Subject Details, and Column Titles) for EXCEL (Unmodified from previous version) ---
        header_lines = [
            'NATIONAL ENGINEERING COLLEGE, K.R. NAGAR, KOVILPATTI – 628 503',
            '(An Autonomous Institution Affiliated to Anna University, Chennai)',
            'NPTEL - Grade Fixing'
        ]
        
        empty_pad_series = pd.Series([''] * num_student_cols, index=student_cols)
        header_rows = []
        header_rows.append(empty_pad_series.copy())
        
        for line in header_lines:
            header_row = empty_pad_series.copy()
            header_row[student_cols[0]] = line
            header_rows.append(header_row)

        header_rows.append(empty_pad_series.copy())
        
        # Course Details Block (Excel uses simple rows)
        details_row_1 = empty_pad_series.copy()
        details_row_1[student_cols[0]] = f"Academic Year: {academic_year.strip()}"
        header_rows.append(details_row_1)

        details_row_2 = empty_pad_series.copy()
        details_row_2[student_cols[0]] = f"Subject Code: {expected_subject.strip()}" 
        header_rows.append(details_row_2)
        
        details_row_3 = empty_pad_series.copy()
        details_row_3[student_cols[0]] = f"Subject Name: {subject_name.strip()}"
        header_rows.append(details_row_3)
        
        details_row_4 = empty_pad_series.copy()
        details_row_4[student_cols[0]] = f"Total Number of Students: {expected_total}" 
        header_rows.append(details_row_4)
        
        header_rows.append(empty_pad_series.copy())
        
        # Column Titles Row
        column_header_row = pd.Series(student_cols, index=student_cols) 
        header_rows.append(column_header_row)
        
        header_df = pd.DataFrame(header_rows)
        header_df.columns = student_cols
        
        # --- 2. Create Summary Table Rows (appended below student list) for EXCEL ---
        summary_start_idx = min(3, num_student_cols - 2) 
        summary_rows = []
        summary_rows.append(empty_pad_series.copy())
        summary_rows.append(empty_pad_series.copy())

        summary_title = empty_pad_series.copy()
        summary_title[student_cols[0]] = f'--- Grading Summary ({grading_method.replace("_", " ").title()}) ---'
        summary_rows.append(summary_title)
        
        # Add header for the summary table (Grade and Mark Range)
        summary_header = empty_pad_series.copy()
        summary_header[student_cols[summary_start_idx]] = 'Grade'
        summary_header[student_cols[summary_start_idx + 1]] = 'Mark Range'
        summary_rows.append(summary_header)
        
        # Add actual range data
        for grade in sorted(continuous_ranges.keys(), key=lambda x: GRADE_POINTS_MAP.get(x, -1), reverse=True):
            range_str = continuous_ranges[grade]
            summary_row = empty_pad_series.copy()
            summary_row[student_cols[summary_start_idx]] = grade
            summary_row[student_cols[summary_start_idx + 1]] = range_str
            summary_rows.append(summary_row)

        summary_data_df = pd.DataFrame(summary_rows)
        
        # --- 3. Combine Header, Student Data, and Summary for EXCEL ---
        df_combined = pd.concat([header_df, df_export, summary_data_df], ignore_index=True)
        df_combined = df_combined.fillna('')

        output = io.BytesIO()
        df_combined.to_excel(output, index=False, header=False, sheet_name='Graded_Results', engine='openpyxl') 
        output.seek(0)

        original_filename = secure_filename(file.filename)
        output_filename = f"{os.path.splitext(original_filename)[0]}_graded.xlsx"

        file_id = str(uuid.uuid4())

        # Store file data and academic details
        processed_files[file_id] = {
            'data': output.getvalue(), 
            'filename': output_filename,
            'dataframe': df_export, # Used for PDF and range calculations
            'grading_method': grading_method,
            'academic_details': { 
                'academic_year': academic_year,
                'subject_code': expected_subject,
                'subject_name': subject_name,
                'expected_total_students': expected_total
            },
            'summary_stats': summary_stats
        }

        return jsonify({
            'message': 'File processed successfully. Output is a single sheet with header, results, and summary.',
            'file_id': file_id,
            'filename': output_filename,
            'summary': summary_stats,
            'details': student_details
        }), 200
        
    except Exception as e:
        if 'Request Entity Too Large' in str(e):
             return jsonify({'error': 'File too large. Maximum size is 16MB.'}), 413
        print(f"Error during upload/processing: {e}")
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

@app.route('/download/<file_id>', methods=['GET'])
def download_specific_file(file_id):
    """Downloads the processed Excel file."""
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


@app.route('/download-pdf/<file_id>', methods=['GET'])
def download_pdf(file_id):
    """
    Generates and returns a PDF file containing all results and summary.
    """
    try:
        if file_id not in processed_files:
            return jsonify({'error': 'File not found or has expired'}), 404
        
        file_info = processed_files[file_id]
        
        # Check cache first
        if 'pdf_data' in file_info:
             pdf_bytes = file_info['pdf_data']
             pdf_filename = file_info['pdf_filename']
        else:
             # Generate the PDF
             pdf_bytes = generate_pdf_from_data(
                 file_info['dataframe'], 
                 file_info['summary_stats'], 
                 file_info['academic_details'],
                 GRADE_POINTS_MAP
             )
             
             # Cache the generated PDF data
             pdf_filename = file_info['filename'].replace('.xlsx', '.pdf')
             processed_files[file_id]['pdf_data'] = pdf_bytes
             processed_files[file_id]['pdf_filename'] = pdf_filename
        
        buffer = io.BytesIO(pdf_bytes)
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=pdf_filename
        )
    
    except Exception as e:
        print(f"PDF Generation/Download Failed: {e}")
        return jsonify({'error': f'PDF generation failed: {str(e)}'}), 500


@app.route('/grade-ranges/<file_id>', methods=['GET'])
def get_grade_ranges(file_id):
    """
    Returns the continuous mark ranges for each grade based on cutoffs.
    """
    try:
        if file_id not in processed_files:
            return jsonify({'error': 'File data not found. Upload a file first.'}), 404
        
        file_info = processed_files[file_id]
        grading_method = file_info['grading_method']
        
        # Recalculate ranges to ensure global state consistency
        continuous_ranges = calculate_continuous_grade_ranges(file_info['dataframe'], grading_method)
        
        return jsonify({
            'file_id': file_id,
            'grading_method': grading_method,
            'grade_ranges': continuous_ranges
        }), 200
    
    except Exception as e:
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500

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