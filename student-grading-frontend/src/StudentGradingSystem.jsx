import React, { useState, useRef } from 'react';
import { Upload, Download, CheckCircle, AlertCircle, FileText, Loader2, X, BarChart3, ListOrdered } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList } from 'recharts';


const StudentGradingSystem = () => {
    const [selectedFile, setSelectedFile] = useState(null);
    const [isUploading, setIsUploading] = useState(false);
    const [notification, setNotification] = useState({ message: '', type: '' });
    const [downloadInfo, setDownloadInfo] = useState(null);
    const [resultStats, setResultStats] = useState(null);
    const [resultDetails, setResultDetails] = useState([]);
    const [chartData, setChartData] = useState([]);
    const [gradeRanges, setGradeRanges] = useState(null); // ⭐Backend-driven range
    const [expectedTotal, setExpectedTotal] = useState('');
    const [subjectCode, setSubjectCode] = useState('');
    const [dragActive, setDragActive] = useState(false);
    const inputRef = useRef(null);


    // Grade order for chart and display
    const gradeSystem = {
        ranges: [
            { grade: 'O', points: 10 }, { grade: 'A+', points: 9 },
            { grade: 'A', points: 8 }, { grade: 'B+', points: 7 },
            { grade: 'B', points: 6 }, { grade: 'C', points: 5 },
            { grade: 'U', points: 0 },
        ],
    };


    const showNotification = (message, type) => {
        setNotification({ message, type });
        setTimeout(() => setNotification({ message: '', type: '' }), 4000);
    };


    const resetState = () => {
        setSelectedFile(null);
        setIsUploading(false);
        setDownloadInfo(null);
        setResultStats(null);
        setResultDetails([]);
        setChartData([]);
        setGradeRanges(null);
        setExpectedTotal('');
        setSubjectCode('');
        if (inputRef.current) inputRef.current.value = '';
    };


    const handleFileSelect = (files) => {
        if (!files || files.length === 0) return;
        const file = files[0];


        if (!file.name.toLowerCase().endsWith('.xlsx') && !file.name.toLowerCase().endsWith('.xls')) {
            showNotification('Please select a valid Excel file (.xlsx or .xls)', 'error');
            return;
        }
        resetState();
        setSelectedFile(file);
    };


    const handleFileChangeEvent = (event) => handleFileSelect(event.target.files);
    const handleDragEvent = (e) => { e.preventDefault(); e.stopPropagation(); };
    const handleDragEnter = (e) => { handleDragEvent(e); setDragActive(true); };
    const handleDragLeave = (e) => { handleDragEvent(e); setDragActive(false); };
    const handleDrop = (e) => {
        handleDragEvent(e);
        setDragActive(false);
        handleFileSelect(e.dataTransfer.files);
    };


    // ⭐ Updated: Fetch grade ranges from backend after upload
    const handleUpload = async () => {
        if (!selectedFile) return showNotification('Please select a file first', 'error');

        if (!expectedTotal || isNaN(Number(expectedTotal)) || Number(expectedTotal) < 0) return showNotification('Please enter a valid expected total number of students', 'error');
        if (!subjectCode || String(subjectCode).trim() === '') return showNotification('Please enter the subject code for verification', 'error');


        setIsUploading(true);
        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('expected_total_students', String(expectedTotal));
        formData.append('subject_code', String(subjectCode).trim());


        try {
            const response = await fetch('http://localhost:5000/upload', { method: 'POST', body: formData });
            const result = await response.json();
            if (!response.ok) {
                // If backend provided a list of found subjects, show it too
                if (result && result.found_subjects) {
                    throw new Error((result.error ? result.error + ' ' : '') + 'Found subjects: ' + result.found_subjects.join(', '));
                }
                throw new Error(result.error || 'Failed to upload file');
            }


            setDownloadInfo({ fileId: result.file_id, filename: result.filename });
            setResultStats(result.summary);
            setResultDetails(result.details);
            showNotification('File processed successfully!', 'success');


            // Bar Chart Data logic
            const gradeOrder = gradeSystem.ranges.map(r => r.grade);
            const counts = result.details.reduce((acc, student) => {
                acc[student.Grade] = (acc[student.Grade] || 0) + 1;
                return acc;
            }, {});
            const formattedChartData = gradeOrder.map(grade => ({
                grade: grade,
                'Number of Students': counts[grade] || 0,
            }));
            setChartData(formattedChartData);


            // ⭐ Fetch grade ranges from backend
            fetch(`http://localhost:5000/grade-ranges/${result.file_id}`)
                .then(res => res.json())
                .then(data => {
                    if (data.grade_ranges) setGradeRanges(data.grade_ranges);
                    else setGradeRanges(null);
                })
                .catch(() => setGradeRanges(null));


        } catch (error) {
            showNotification(error.message, 'error');
        } finally {
            setIsUploading(false);
        }
    };


    const handleDownload = async () => {
        if (!downloadInfo) return showNotification('No file available for download.', 'error');
        try {
            const response = await fetch(`http://localhost:5000/download/${downloadInfo.fileId}`);
            if (!response.ok) throw new Error('Download failed from server.');
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = downloadInfo.filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
            showNotification('File downloaded successfully!', 'success');
        } catch (error) {
            showNotification(error.message, 'error');
        }
    };


    const getGradePillStyle = (grade) => {
        switch (grade) {
            case 'O': case 'A+': case 'A': return 'bg-green-100 text-green-800';
            case 'U': return 'bg-red-100 text-red-800';
            default: return 'bg-slate-100 text-slate-800';
        }
    };


    const StatCard = ({ title, value }) => (
        <div className="border border-slate-200 bg-white p-4">
            <p className="text-sm text-slate-500 font-medium">{title}</p>
            <p className="text-2xl font-semibold text-slate-800">{value}</p>
        </div>
    );


    return (
        <div className="min-h-screen bg-slate-50 flex flex-col items-center py-10 px-4 sm:px-8">
            {notification.message && (
                <div className={`fixed top-5 right-5 z-50 flex items-center gap-3 p-4 shadow-lg text-white ${notification.type === 'success' ? 'bg-green-600' : 'bg-red-600'}`}>
                    {notification.type === 'success' ? <CheckCircle className="w-5 h-5" /> : <AlertCircle className="w-5 h-5" />}
                    <span>{notification.message}</span>
                </div>
            )}


            <div className="w-full max-w-6xl mx-auto">
                <header className="text-center mb-10">
                    <h1 className="text-4xl sm:text-5xl font-bold text-slate-900 mb-2">Student Grading System</h1>
                    <p className="text-lg text-slate-600">Upload an Excel file to automatically process and grade student marks.</p>
                </header>


                <main className="bg-white shadow border border-slate-200 p-8 mb-8">
                    <div
                        className={`relative border-2 border-dashed transition-colors duration-200 ${dragActive ? 'border-blue-500 bg-blue-50' : selectedFile ? 'border-green-500 bg-green-50' : 'border-slate-300 bg-slate-50 hover:border-slate-400'}`}
                        onDragEnter={handleDragEnter} onDragLeave={handleDragLeave} onDragOver={handleDragEvent} onDrop={handleDrop}
                    >
                        <input id="file-input" ref={inputRef} type="file" accept=".xlsx,.xls" onChange={handleFileChangeEvent} className="hidden" />
                        <label htmlFor="file-input" className="flex flex-col items-center justify-center w-full h-48 cursor-pointer group p-4">
                            {selectedFile ? (
                                <>
                                    <FileText className="w-12 h-12 text-green-500 mb-3" />
                                    <p className="text-green-800 font-medium text-center break-all">{selectedFile.name}</p>
                                    <p className="text-sm text-green-600 mt-1">Click here or drag a new file to replace</p>
                                </>
                            ) : (
                                <>
                                    <Upload className="w-12 h-12 mb-3 text-slate-400 group-hover:text-blue-500" />
                                    <p className="text-base text-slate-600"><span className="font-semibold text-blue-600">Click to upload</span> or drag and drop</p>
                                    <p className="text-sm text-slate-500 mt-1">Excel files only (.xlsx or .xls)</p>
                                </>
                            )}
                        </label>
                    </div>


                    {selectedFile && (
                        <div className="mt-6 p-3 bg-slate-100 border border-slate-200 flex items-center justify-between">
                            <span className="text-sm font-medium text-slate-800 truncate pr-4">{selectedFile.name}</span>
                            <button onClick={resetState} className="text-slate-500 hover:text-slate-800 font-semibold text-sm flex items-center gap-1 flex-shrink-0">
                                <X className="w-4 h-4" /> Remove
                            </button>
                        </div>
                    )}


                    <div className="mt-6 space-y-4">
                        {selectedFile && (
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                <div>
                                    <label className="block text-sm font-medium text-slate-700">Expected total students</label>
                                    <input type="number" min="0" value={expectedTotal} onChange={(e) => setExpectedTotal(e.target.value)} placeholder="e.g. 45" className="mt-1 block w-full rounded-md border-slate-200 shadow-sm focus:border-blue-500 focus:ring-blue-500 p-2" />
                                    <p className="text-xs text-slate-400 mt-1">Enter the expected number of students in the sheet for verification.</p>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-slate-700">Subject code</label>
                                    <input type="text" value={subjectCode} onChange={(e) => setSubjectCode(e.target.value)} placeholder="e.g. CS101" className="mt-1 block w-full rounded-md border-slate-200 shadow-sm focus:border-blue-500 focus:ring-blue-500 p-2" />
                                    <p className="text-xs text-slate-400 mt-1">Enter the subject code to verify the uploaded sheet matches.</p>
                                </div>
                            </div>
                        )}
                        <button onClick={handleUpload} disabled={!selectedFile || isUploading} className="w-full flex items-center justify-center gap-2 px-6 py-3 font-semibold shadow-sm text-base bg-blue-600 text-white hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed transition-colors">
                            {isUploading ? <><Loader2 className="w-5 h-5 animate-spin" /> Processing...</> : <><Upload className="w-5 h-5" /> Upload & Process File</>}
                        </button>
                        {downloadInfo && (
                            <button onClick={handleDownload} className="w-full flex items-center justify-center gap-2 px-6 py-3 bg-green-600 text-white font-semibold shadow-sm hover:bg-green-700 transition-colors">
                                <Download className="w-5 h-5" /> Download Graded File
                            </button>
                        )}
                    </div>
                </main>


                {resultStats && resultDetails.length > 0 &&
                    <div className="space-y-8">
                        <section>
                            <h2 className="text-2xl font-semibold mb-4 text-slate-800 flex items-center gap-2"><BarChart3 /> Results Summary</h2>
                            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                                <StatCard title="Total Students" value={resultStats.count} />
                                <StatCard title="Average Marks" value={resultStats.average} />
                                <StatCard title="Highest Marks" value={resultStats.max} />
                                <StatCard title="Lowest Marks" value={resultStats.min} />
                                <StatCard title="Grading Method" value={resultStats.grading_method?.replace(/_/g, ' ')} />
                            </div>
                        </section>


                        {/* ⭐ GRADE MARK RANGES using backend*/}
                        <section>
                            <h2 className="text-2xl font-semibold mb-4 text-slate-800 flex items-center gap-2"><ListOrdered /> Grade Mark Ranges</h2>
                            {gradeRanges ? (
                                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4">
                                    {gradeSystem.ranges.map(({ grade }) => (
                                        gradeRanges[grade] && (
                                            <div key={grade} className="border border-slate-200 bg-white p-4 text-center">
                                                <p className={`mx-auto mb-2 w-10 h-10 flex items-center justify-center text-sm font-bold ${getGradePillStyle(grade)} rounded-full`}>{grade}</p>
                                                <p className="text-lg font-semibold text-slate-800">{gradeRanges[grade]}</p>
                                            </div>
                                        )
                                    ))}
                                </div>
                            ) : null}
                        </section>


                        <section className="bg-white shadow border border-slate-200">
                            <h2 className="text-2xl font-semibold text-slate-800 p-6 border-b border-slate-200 flex items-center gap-2">
                                <BarChart3 /> Grade Distribution
                            </h2>
                            <div className="p-6">
                                <ResponsiveContainer width="100%" height={350}>
                                    <BarChart data={chartData} margin={{ top: 25, right: 20, left: -10, bottom: 5 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                                        <XAxis dataKey="grade" stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} />
                                        <YAxis allowDecimals={false} stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} />
                                        <Tooltip cursor={{ fill: '#f1f5f9' }} contentStyle={{ border: '1px solid #e2e8f0' }} />
                                        <Bar dataKey="Number of Students" fill="#2563eb" barSize={40}>
                                            <LabelList dataKey="Number of Students" position="top" />
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        </section>


                        <section className="bg-white shadow border border-slate-200">
                            <h2 className="text-2xl font-semibold text-slate-800 p-6 border-b border-slate-200">Student Details</h2>
                            <div className="max-h-[60vh] overflow-y-auto">
                                <table className="min-w-full divide-y divide-slate-200">
                                    <thead className="bg-slate-50 sticky top-0">
                                        <tr>
                                            <th className="px-6 py-4 whitespace-nowrap text-sm text-slate-600 uppercase tracking-wider">Name</th>
                                            <th className="px-6 py-4 whitespace-nowrap text-sm text-slate-600 uppercase tracking-wider">Marks</th>
                                            <th className="px-6 py-4 whitespace-nowrap text-sm text-slate-600 uppercase tracking-wider">Grade</th>
                                            <th className="px-6 py-4 whitespace-nowrap text-sm text-slate-600 uppercase tracking-wider">Grade Points</th>
                                        </tr>
                                    </thead>
                                    <tbody className="bg-white divide-y divide-slate-200">
                                        {resultDetails.map((student, idx) => (
                                            <tr key={idx} className="hover:bg-slate-50 transition-colors">
                                                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-slate-900">{student.Name}</td>
                                                <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-600">{student.Marks ?? 'N/A'}</td>
                                                <td className="px-6 py-4 whitespace-nowrap">
                                                    <span className={`px-2.5 py-0.5 text-xs font-semibold rounded-md ${getGradePillStyle(student.Grade)}`}>{student.Grade}</span>
                                                </td>
                                                <td className="px-6 py-4 whitespace-nowrap text-sm text-slate-600">{student.Grade_Points ?? '-'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </section>
                    </div>
                }
            </div>
        </div>
    );
};


export default StudentGradingSystem;
