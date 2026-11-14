import { useEffect, useRef, useState } from "react";
import {
  uploadImport,
  revalidateImport,
  processImport,
  listImportHistory,
  downloadImportErrors,
  downloadImportTemplate,
  getImportStatus,
  cancelImport,
} from "../api/imports";
import type {
  ImportType,
  ImportUploadResponse,
  ImportJob,
  ImportResult,
  ImportProgress,
  DuplicateStrategy,
} from "../types";

interface ImportTypeConfig {
  value: ImportType;
  label: string;
  description: string;
  required: string[];
  optional: string[];
}

const IMPORT_TYPES: ImportTypeConfig[] = [
  {
    value: "PRODUCTS",
    label: "Products",
    description: "Product catalog items with SKU, categories, prices, and initial stock",
    required: ["Product Name", "SKU", "Category", "Unit Price", "Stock Quantity"],
    optional: ["Brand", "Description", "Cost Price", "Unit of Measure"],
  },
  {
    value: "INVENTORY",
    label: "Inventory",
    description: "Stock adjustments, warehouse counts, and reorder level thresholds",
    required: ["Product SKU", "Stock Quantity"],
    optional: ["Reorder Level", "Remarks"],
  },
  {
    value: "CUSTOMERS",
    label: "Customers",
    description: "Customer directory profiles with contact info and classifications",
    required: ["Name", "Email", "Phone"],
    optional: ["Address", "City", "State", "Country", "Postal Code", "Customer Type"],
  },
  {
    value: "SALES",
    label: "Sales Transactions",
    description: "Completed sales orders, line items, payment modes, and invoice logs",
    required: ["Customer", "Product", "Quantity", "Unit Price", "Sale Date"],
    optional: ["Invoice Number", "Sales Channel", "Payment Method"],
  },
];

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB

type Stage = "idle" | "uploading" | "validating" | "ready" | "processing" | "done";

const STATUS_STYLES: Record<string, string> = {
  PENDING: "bg-slate-100 text-slate-700 border-slate-300",
  UPLOADED: "bg-blue-50 text-blue-700 border-blue-200",
  VALIDATED: "bg-indigo-50 text-indigo-700 border-indigo-200",
  PROCESSING: "bg-amber-50 text-amber-700 border-amber-300 animate-pulse",
  COMPLETED: "bg-emerald-50 text-emerald-700 border-emerald-300",
  COMPLETED_WITH_ERRORS: "bg-amber-50 text-amber-700 border-amber-300",
  FAILED: "bg-red-50 text-red-700 border-red-300",
  CANCELLED: "bg-slate-100 text-slate-600 border-slate-300",
};

export default function DataImport() {
  const [importType, setImportType] = useState<ImportType>("PRODUCTS");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [stage, setStage] = useState<Stage>("idle");

  const [uploadResult, setUploadResult] = useState<ImportUploadResponse | null>(null);
  const [duplicateStrategy, setDuplicateStrategy] = useState<DuplicateStrategy>("SKIP");
  const [activeTab, setActiveTab] = useState<"preview" | "issues">("preview");
  const [issueFilter, setIssueFilter] = useState<"all" | "INVALID" | "DUPLICATE">("all");

  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [processResult, setProcessResult] = useState<ImportResult | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const [history, setHistory] = useState<ImportJob[]>([]);
  const [historyFilter, setHistoryFilter] = useState<string>("ALL");
  const [historyLoading, setHistoryLoading] = useState(true);
  const [selectedJobDetail, setSelectedJobDetail] = useState<ImportJob | null>(null);
  const [downloadingTemplate, setDownloadingTemplate] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollingRef = useRef<any>(null);

  const currentTypeDef = IMPORT_TYPES.find((t) => t.value === importType)!;

  const loadHistory = async (typeFilter?: string) => {
    setHistoryLoading(true);
    try {
      const filter = typeFilter && typeFilter !== "ALL" ? (typeFilter as ImportType) : undefined;
      const data = await listImportHistory(filter);
      setHistory(data.items);
    } catch {
      // non-blocking
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => {
    loadHistory(historyFilter);
  }, [historyFilter]);

  // Clean up polling on unmount
  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const resetWorkflow = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    setFile(null);
    setFileError(null);
    setStage("idle");
    setUploadResult(null);
    setProgress(null);
    setProcessResult(null);
    setApiError(null);
    setActiveTab("preview");
    setIssueFilter("all");
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleTypeChange = (t: ImportType) => {
    setImportType(t);
    resetWorkflow();
  };

  const validateFile = (selected: File | null) => {
    setFileError(null);
    setApiError(null);
    if (!selected) {
      setFile(null);
      return;
    }
    if (!selected.name.toLowerCase().endsWith(".csv")) {
      setFileError("Only .csv files are supported. Please convert your file to CSV format.");
      setFile(null);
      return;
    }
    if (selected.size > MAX_FILE_SIZE) {
      setFileError("File exceeds the 10 MB limit. Please split your dataset into smaller files.");
      setFile(null);
      return;
    }
    if (selected.size === 0) {
      setFileError("The selected file is empty (0 bytes).");
      setFile(null);
      return;
    }
    setFile(selected);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      validateFile(e.dataTransfer.files[0]);
    }
  };

  const handleDownloadTemplate = async (type: ImportType) => {
    setDownloadingTemplate(type);
    try {
      await downloadImportTemplate(type);
    } catch (err: any) {
      setApiError("Failed to download template. Please try again.");
    } finally {
      setDownloadingTemplate(null);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setApiError(null);
    setStage("uploading");
    try {
      setStage("validating");
      const result = await uploadImport(importType, file);
      setUploadResult(result);
      setProcessResult(null);
      setProgress(null);
      setStage("ready");
      if (result.row_issues && result.row_issues.length > 0 && result.summary.valid_records === 0) {
        setActiveTab("issues");
      }
    } catch (err: any) {
      setApiError(err?.response?.data?.detail ?? "Failed to upload and validate file.");
      setStage("idle");
    }
  };

  const handleRevalidate = async () => {
    if (!uploadResult) return;
    setApiError(null);
    setStage("validating");
    try {
      const result = await revalidateImport(uploadResult.import_id);
      setUploadResult(result);
      setStage("ready");
    } catch (err: any) {
      setApiError(err?.response?.data?.detail ?? "Failed to re-validate file.");
      setStage("ready");
    }
  };

  const handleStartImport = async () => {
    if (!uploadResult) return;
    setApiError(null);
    setStage("processing");

    try {
      const initial = await processImport(uploadResult.import_id, duplicateStrategy);
      setProcessResult(initial);
      setProgress({
        id: initial.id,
        status: initial.status,
        total_records: initial.total_records,
        processed_records: initial.processed_records ?? 0,
        progress_percentage: 0,
        successful_records: initial.successful_records ?? 0,
        failed_records: initial.failed_records ?? 0,
        duplicate_records: initial.duplicate_records ?? 0,
        skipped_records: initial.skipped_records ?? 0,
        duration_seconds: null,
      });

      // Start background status polling
      const pollInterval = setInterval(async () => {
        try {
          const statusData = await getImportStatus(uploadResult.import_id);
          setProgress(statusData);

          if (
            statusData.status === "COMPLETED" ||
            statusData.status === "COMPLETED_WITH_ERRORS" ||
            statusData.status === "FAILED" ||
            statusData.status === "CANCELLED"
          ) {
            clearInterval(pollInterval);
            pollingRef.current = null;
            setProcessResult({
              id: statusData.id,
              status: statusData.status,
              total_records: statusData.total_records,
              successful_records: statusData.successful_records,
              failed_records: statusData.failed_records,
              duplicate_records: statusData.duplicate_records,
              skipped_records: statusData.skipped_records,
              processed_records: statusData.processed_records,
              duration_seconds: statusData.duration_seconds,
            });
            setStage("done");
            loadHistory(historyFilter);
          }
        } catch {
          // keep polling or handled gracefully
        }
      }, 1200);

      pollingRef.current = pollInterval;
    } catch (err: any) {
      setApiError(err?.response?.data?.detail ?? "Failed to start import processing.");
      setStage("ready");
    }
  };

  const handleCancel = async () => {
    if (!uploadResult) return;
    try {
      await cancelImport(uploadResult.import_id);
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
      const finalStatus = await getImportStatus(uploadResult.import_id);
      setProgress(finalStatus);
      setStage("done");
      loadHistory(historyFilter);
    } catch (err: any) {
      setApiError(err?.response?.data?.detail ?? "Failed to cancel import.");
    }
  };

  const summary = uploadResult?.summary;
  const filteredIssues = uploadResult?.row_issues.filter((issue) => {
    if (issueFilter === "all") return true;
    return issue.status === issueFilter;
  }) ?? [];

  return (
    <div className="mx-auto w-full max-w-6xl p-4 sm:p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-200 pb-5">
        <div>
          <h1 className="text-2xl font-bold text-navy flex items-center gap-2.5">
            <svg className="h-7 w-7 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            Advanced Data Import & Bulk Processing
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Upload, validate, preview, and reliably process bulk datasets into RetailPulse with duplicate control and audit tracking.
          </p>
        </div>
      </div>

      {apiError && (
        <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <svg className="h-5 w-5 shrink-0 text-red-500 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div className="flex-1">{apiError}</div>
          <button onClick={() => setApiError(null)} className="text-red-400 hover:text-red-600 font-bold">×</button>
        </div>
      )}

      {/* Template Download Center */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-base font-semibold text-navy">CSV Templates Center</h2>
            <p className="text-xs text-slate-500">
              Download formatted templates with sample data to ensure your CSV headers match the validation rules.
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {IMPORT_TYPES.map((t) => (
            <div
              key={t.value}
              className={`flex flex-col justify-between rounded-lg border p-3.5 transition-all ${
                importType === t.value
                  ? "border-brand-300 bg-brand-50/30 ring-1 ring-brand-300"
                  : "border-slate-200 bg-slate-50/50 hover:bg-slate-50"
              }`}
            >
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-slate-800">{t.label}</span>
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">CSV</span>
                </div>
                <p className="mt-1 text-xs text-slate-500 line-clamp-2">{t.description}</p>
              </div>
              <button
                onClick={() => handleDownloadTemplate(t.value)}
                disabled={downloadingTemplate === t.value}
                className="mt-3 inline-flex items-center justify-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                <svg className="h-3.5 w-3.5 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                {downloadingTemplate === t.value ? "Downloading..." : "Download Template"}
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Step 1: Select Type & Upload */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-navy mb-3">1. Select Entity & Upload File</h2>
        
        {/* Import Type Pills */}
        <div className="flex flex-wrap gap-2 mb-4">
          {IMPORT_TYPES.map((t) => (
            <button
              key={t.value}
              onClick={() => handleTypeChange(t.value)}
              disabled={stage === "processing"}
              className={`rounded-lg px-4 py-2 text-sm font-semibold transition-all ${
                importType === t.value
                  ? "bg-brand-500 text-white shadow-sm ring-2 ring-brand-500/20"
                  : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Required Columns Info */}
        <div className="mb-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-600 border border-slate-200">
          <div className="flex items-center gap-1.5 font-medium text-slate-800 mb-1">
            <svg className="h-4 w-4 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Expected Columns for {currentTypeDef.label}:
          </div>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {currentTypeDef.required.map((col) => (
              <span key={col} className="rounded bg-white px-2 py-0.5 border border-slate-300 font-mono text-[11px] text-slate-700">
                {col} <span className="text-red-500">*</span>
              </span>
            ))}
            {currentTypeDef.optional.map((col) => (
              <span key={col} className="rounded bg-white px-2 py-0.5 border border-slate-200 font-mono text-[11px] text-slate-500">
                {col}
              </span>
            ))}
          </div>
        </div>

        {/* Drag and Drop Zone */}
        {!file ? (
          <div
            onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 text-center transition-all ${
              isDragging
                ? "border-brand-500 bg-brand-50/50"
                : "border-slate-300 hover:border-brand-400 hover:bg-slate-50/60"
            }`}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(e) => validateFile(e.target.files?.[0] ?? null)}
            />
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-50 text-brand-500 mb-3">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
            </div>
            <p className="text-sm font-semibold text-slate-800">
              Click to select or drag and drop your CSV file here
            </p>
            <p className="mt-1 text-xs text-slate-400">Supported format: .csv (Max size: 10 MB)</p>
          </div>
        ) : (
          <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-100 text-xs font-bold text-brand-600">
                CSV
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-800">{file.name}</p>
                <p className="text-xs text-slate-500">{(file.size / 1024).toFixed(1)} KB</p>
              </div>
            </div>
            <button
              onClick={resetWorkflow}
              disabled={stage === "processing"}
              className="text-xs font-semibold text-red-600 hover:underline px-3 py-1"
            >
              Remove
            </button>
          </div>
        )}

        {fileError && <p className="mt-2 text-xs font-medium text-red-600">{fileError}</p>}

        <div className="mt-4 flex items-center gap-3">
          <button
            className="btn-primary"
            disabled={!file || stage === "uploading" || stage === "validating" || stage === "processing"}
            onClick={handleUpload}
          >
            {stage === "uploading" || stage === "validating" ? (
              <span className="flex items-center gap-2">
                <svg className="h-4 w-4 animate-spin text-white" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                </svg>
                Validating File...
              </span>
            ) : (
              "Upload & Validate"
            )}
          </button>
          {uploadResult && stage !== "processing" && (
            <button className="btn-outline" onClick={resetWorkflow}>
              Start Over
            </button>
          )}
        </div>
      </div>

      {/* Step 2: Preview & Validation Results */}
      {uploadResult && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-5">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-100 pb-3">
            <div>
              <h2 className="text-base font-semibold text-navy">2. Import Preview & Validation Summary</h2>
              <p className="text-xs text-slate-500">File: {uploadResult.filename}</p>
            </div>
            <button
              onClick={handleRevalidate}
              disabled={stage === "processing"}
              className="text-xs font-medium text-brand-600 hover:underline flex items-center gap-1 self-start sm:self-auto"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Re-validate against current DB
            </button>
          </div>

          {/* Stat Cards */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <SummaryStat label="Total Rows" value={summary!.total_records} />
            <SummaryStat label="Valid Rows" value={summary!.valid_records} accent="text-emerald-600" />
            <SummaryStat label="Invalid Rows" value={summary!.invalid_records} accent="text-red-600" />
            <SummaryStat label="Duplicate Rows" value={summary!.duplicate_records} accent="text-amber-600" />
          </div>

          {/* Validation Notice */}
          {summary!.invalid_records > 0 || summary!.duplicate_records > 0 ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-900 flex items-start gap-2">
              <svg className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
              <div>
                <span className="font-semibold">Validation Alert: </span>
                {summary!.invalid_records > 0 && `${summary!.invalid_records} row(s) contain invalid fields and cannot be imported. `}
                {summary!.duplicate_records > 0 && `${summary!.duplicate_records} row(s) match existing records in the database or within the file. `}
                Choose your duplicate strategy below before proceeding.
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 p-3 text-xs text-emerald-900 flex items-center gap-2">
              <svg className="h-4 w-4 text-emerald-600 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              <span>All {summary!.valid_records} row(s) are valid and ready to be processed!</span>
            </div>
          )}

          {/* Tab Selection */}
          <div className="border-b border-slate-200 flex gap-4">
            <button
              onClick={() => setActiveTab("preview")}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors ${
                activeTab === "preview"
                  ? "border-brand-500 text-brand-600"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              }`}
            >
              Sample Data Preview ({uploadResult.preview_rows.length} rows)
            </button>
            <button
              onClick={() => setActiveTab("issues")}
              className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === "issues"
                  ? "border-brand-500 text-brand-600"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              }`}
            >
              Problem Rows
              <span className={`rounded-full px-1.5 py-0.2 text-[10px] ${
                uploadResult.row_issues.length > 0 ? "bg-red-100 text-red-700" : "bg-slate-100 text-slate-600"
              }`}>
                {uploadResult.row_issues.length}
              </span>
            </button>
          </div>

          {/* Tab 1: Preview Table */}
          {activeTab === "preview" && (
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-600 font-semibold border-b border-slate-200">
                  <tr>
                    <th className="px-3 py-2.5 whitespace-nowrap">#</th>
                    {uploadResult.detected_columns.map((col) => (
                      <th key={col} className="px-3 py-2.5 whitespace-nowrap">{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {uploadResult.preview_rows.map((row) => (
                    <tr key={row.row_number} className="hover:bg-slate-50/70">
                      <td className="px-3 py-2 text-slate-400 font-mono">{row.row_number}</td>
                      {uploadResult.detected_columns.map((col) => (
                        <td key={col} className="px-3 py-2 whitespace-nowrap text-slate-700">
                          {row.data[col] ?? "—"}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Tab 2: Issues Table */}
          {activeTab === "issues" && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-500 font-medium">Filter by:</span>
                  {(["all", "INVALID", "DUPLICATE"] as const).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setIssueFilter(mode)}
                      className={`rounded px-2.5 py-1 text-xs font-semibold ${
                        issueFilter === mode
                          ? "bg-slate-800 text-white"
                          : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                      }`}
                    >
                      {mode === "all" ? "All Issues" : mode}
                    </button>
                  ))}
                </div>
              </div>

              {filteredIssues.length === 0 ? (
                <p className="text-xs text-slate-400 py-6 text-center">No issues matching this filter.</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-50 text-slate-600 font-semibold border-b border-slate-200">
                      <tr>
                        <th className="px-3 py-2.5">Row</th>
                        <th className="px-3 py-2.5">Issue Type</th>
                        <th className="px-3 py-2.5">Error Reason</th>
                        <th className="px-3 py-2.5">Raw Data Snapshot</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {filteredIssues.map((issue) => (
                        <tr key={issue.row_number} className="hover:bg-slate-50/70">
                          <td className="px-3 py-2 font-mono font-medium text-slate-700">{issue.row_number}</td>
                          <td className="px-3 py-2">
                            <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold ${
                              issue.status === "DUPLICATE"
                                ? "bg-amber-100 text-amber-800"
                                : "bg-red-100 text-red-800"
                            }`}>
                              {issue.status}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-slate-700 font-medium">{issue.message}</td>
                          <td className="px-3 py-2 text-slate-500 font-mono text-[11px] truncate max-w-xs">
                            {JSON.stringify(issue.data)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Step 3: Duplicate Strategy Selection & Actions */}
          <div className="border-t border-slate-100 pt-5 space-y-4">
            <h3 className="text-sm font-semibold text-navy">3. Duplicate Handling Strategy</h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {[
                {
                  value: "SKIP",
                  title: "Skip Duplicates",
                  desc: "Duplicate records will be safely ignored and not imported.",
                },
                {
                  value: "UPDATE",
                  title: "Update Existing",
                  desc: "Overwrite matching existing records with updated CSV values.",
                },
                {
                  value: "REJECT",
                  title: "Reject as Errors",
                  desc: "Treat duplicates as validation failures and log them to error report.",
                },
              ].map((opt) => (
                <label
                  key={opt.value}
                  className={`flex cursor-pointer flex-col rounded-lg border p-3.5 transition-all ${
                    duplicateStrategy === opt.value
                      ? "border-brand-500 bg-brand-50/20 ring-2 ring-brand-500/20"
                      : "border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="duplicateStrategy"
                      value={opt.value}
                      checked={duplicateStrategy === opt.value}
                      onChange={() => setDuplicateStrategy(opt.value as DuplicateStrategy)}
                      disabled={stage === "processing"}
                      className="text-brand-500 focus:ring-brand-500"
                    />
                    <span className="text-xs font-bold text-slate-800">{opt.title}</span>
                  </div>
                  <p className="mt-1 pl-5 text-[11px] text-slate-500">{opt.desc}</p>
                </label>
              ))}
            </div>

            {/* Start Button */}
            <div className="flex items-center gap-3 pt-2">
              <button
                className="btn-primary"
                disabled={summary!.valid_records === 0 || stage === "processing"}
                onClick={handleStartImport}
              >
                {stage === "processing" ? "Processing..." : `Confirm & Start Import (${summary!.valid_records} Valid Rows)`}
              </button>
              {summary!.valid_records === 0 && (
                <span className="text-xs text-red-600 font-medium">
                  No valid rows to import. Please correct the CSV and re-upload.
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Step 4: Active Processing & Progress Bar */}
      {stage === "processing" && progress && (
        <div className="rounded-xl border border-amber-300 bg-amber-50/50 p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <svg className="h-5 w-5 animate-spin text-amber-600" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
              </svg>
              <div>
                <h3 className="text-sm font-bold text-amber-950">Bulk Import in Progress</h3>
                <p className="text-xs text-amber-800">
                  Processing row by row in background. You can navigate away or track progress below.
                </p>
              </div>
            </div>
            <button
              onClick={handleCancel}
              className="rounded-md border border-red-300 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-50 shadow-sm"
            >
              Cancel Import
            </button>
          </div>

          {/* Progress Bar */}
          <div>
            <div className="flex justify-between text-xs font-medium text-slate-700 mb-1">
              <span>{progress.processed_records} of {progress.total_records} rows processed</span>
              <span>{progress.progress_percentage}%</span>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full bg-brand-500 transition-all duration-300 ease-out"
                style={{ width: `${progress.progress_percentage}%` }}
              ></div>
            </div>
          </div>

          {/* Progress Counters */}
          <div className="grid grid-cols-3 gap-2 text-center text-xs">
            <div className="rounded bg-white p-2 border border-slate-200">
              <span className="text-slate-400 block text-[10px] uppercase font-bold">Successful</span>
              <span className="text-sm font-bold text-emerald-600">{progress.successful_records}</span>
            </div>
            <div className="rounded bg-white p-2 border border-slate-200">
              <span className="text-slate-400 block text-[10px] uppercase font-bold">Skipped</span>
              <span className="text-sm font-bold text-slate-600">{progress.skipped_records}</span>
            </div>
            <div className="rounded bg-white p-2 border border-slate-200">
              <span className="text-slate-400 block text-[10px] uppercase font-bold">Failed</span>
              <span className="text-sm font-bold text-red-600">{progress.failed_records}</span>
            </div>
          </div>
        </div>
      )}

      {/* Step 5: Completion Summary */}
      {stage === "done" && processResult && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between border-b border-slate-100 pb-3">
            <div className="flex items-center gap-2">
              <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-bold ${
                STATUS_STYLES[processResult.status] ?? "bg-slate-100 text-slate-700"
              }`}>
                {processResult.status.replace(/_/g, " ")}
              </span>
              <h2 className="text-base font-bold text-navy">Import Execution Complete</h2>
            </div>
            <span className="text-xs text-slate-500">
              Duration: {processResult.duration_seconds ? `${processResult.duration_seconds.toFixed(1)}s` : "completed"}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <SummaryStat label="Total Processed" value={processResult.total_records} />
            <SummaryStat label="Successful" value={processResult.successful_records} accent="text-emerald-600" />
            <SummaryStat label="Skipped / Dupes" value={processResult.skipped_records ?? processResult.duplicate_records} accent="text-amber-600" />
            <SummaryStat label="Failed" value={processResult.failed_records} accent="text-red-600" />
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-2">
            {(processResult.failed_records > 0 || (processResult.skipped_records ?? 0) > 0 || processResult.duplicate_records > 0) && (
              <button
                className="btn-outline inline-flex items-center gap-1.5"
                onClick={() => downloadImportErrors(processResult.id, uploadResult?.filename ?? "import_errors")}
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Download Error Report (CSV)
              </button>
            )}
            <button className="btn-primary" onClick={resetWorkflow}>
              Import Another File
            </button>
          </div>
        </div>
      )}

      {/* Step 6: Import History */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-navy">Import History & Audit Log</h2>
            <p className="text-xs text-slate-500">Historical record of all bulk processing jobs executed for this company.</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">Filter type:</span>
            <select
              value={historyFilter}
              onChange={(e) => setHistoryFilter(e.target.value)}
              className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 outline-none focus:border-brand-500"
            >
              <option value="ALL">All Types</option>
              <option value="PRODUCTS">Products</option>
              <option value="INVENTORY">Inventory</option>
              <option value="CUSTOMERS">Customers</option>
              <option value="SALES">Sales</option>
            </select>
          </div>
        </div>

        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 font-semibold text-slate-600 border-b border-slate-200 uppercase tracking-wide">
              <tr>
                <th className="px-3.5 py-2.5">Type</th>
                <th className="px-3.5 py-2.5">Filename</th>
                <th className="px-3.5 py-2.5">Uploaded By</th>
                <th className="px-3.5 py-2.5">Date</th>
                <th className="px-3.5 py-2.5 text-center">Total</th>
                <th className="px-3.5 py-2.5 text-center text-emerald-600">Success</th>
                <th className="px-3.5 py-2.5 text-center text-red-600">Failed</th>
                <th className="px-3.5 py-2.5 text-center">Status</th>
                <th className="px-3.5 py-2.5 text-center">Duration</th>
                <th className="px-3.5 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {historyLoading && (
                <tr>
                  <td colSpan={10} className="px-4 py-8 text-center text-slate-400">Loading import history...</td>
                </tr>
              )}
              {!historyLoading && history.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-4 py-8 text-center text-slate-400">No import records found.</td>
                </tr>
              )}
              {history.map((job) => (
                <tr key={job.id} className="hover:bg-slate-50/70">
                  <td className="px-3.5 py-2.5 font-medium text-slate-800">{job.import_type}</td>
                  <td className="px-3.5 py-2.5 max-w-[160px] truncate text-slate-700 font-mono" title={job.filename}>
                    {job.filename}
                  </td>
                  <td className="px-3.5 py-2.5 text-slate-600">{job.uploaded_by_name ?? "—"}</td>
                  <td className="px-3.5 py-2.5 text-slate-500 whitespace-nowrap">
                    {new Date(job.created_at).toLocaleString()}
                  </td>
                  <td className="px-3.5 py-2.5 text-center font-medium text-slate-700">{job.total_records}</td>
                  <td className="px-3.5 py-2.5 text-center font-medium text-emerald-600">{job.successful_records}</td>
                  <td className="px-3.5 py-2.5 text-center font-medium text-red-600">{job.failed_records}</td>
                  <td className="px-3.5 py-2.5 text-center">
                    <span className={`inline-block rounded-full border px-2 py-0.5 text-[10px] font-bold ${
                      STATUS_STYLES[job.status] ?? "bg-slate-100 text-slate-700"
                    }`}>
                      {job.status.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td className="px-3.5 py-2.5 text-center text-slate-500">
                    {job.duration_seconds ? `${job.duration_seconds.toFixed(1)}s` : "—"}
                  </td>
                  <td className="px-3.5 py-2.5 text-right whitespace-nowrap space-x-2">
                    <button
                      onClick={() => setSelectedJobDetail(job)}
                      className="text-xs font-semibold text-brand-600 hover:underline"
                    >
                      Details
                    </button>
                    {(job.failed_records > 0 || (job.skipped_records ?? 0) > 0 || job.duplicate_records > 0) && (
                      <button
                        onClick={() => downloadImportErrors(job.id, job.filename)}
                        className="text-xs font-semibold text-slate-600 hover:text-slate-900 hover:underline"
                      >
                        Errors CSV
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Detail Modal */}
      {selectedJobDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-xs">
          <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-6 shadow-xl space-y-4">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div>
                <h3 className="text-base font-bold text-navy">Import Job Details</h3>
                <p className="text-xs text-slate-500 font-mono">{selectedJobDetail.id}</p>
              </div>
              <button
                onClick={() => setSelectedJobDetail(null)}
                className="text-slate-400 hover:text-slate-700 font-bold text-lg"
              >
                ×
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Import Type</span>
                <span className="font-semibold text-slate-800">{selectedJobDetail.import_type}</span>
              </div>
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Status</span>
                <span className="font-semibold text-slate-800">{selectedJobDetail.status}</span>
              </div>
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Filename</span>
                <span className="font-mono text-slate-800 truncate block">{selectedJobDetail.filename}</span>
              </div>
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Duplicate Strategy</span>
                <span className="font-semibold text-slate-800">{selectedJobDetail.duplicate_strategy ?? "SKIP"}</span>
              </div>
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Total Records</span>
                <span className="font-bold text-slate-800">{selectedJobDetail.total_records}</span>
              </div>
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Successful</span>
                <span className="font-bold text-emerald-600">{selectedJobDetail.successful_records}</span>
              </div>
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Failed</span>
                <span className="font-bold text-red-600">{selectedJobDetail.failed_records}</span>
              </div>
              <div className="rounded bg-slate-50 p-2.5 border border-slate-200">
                <span className="text-slate-500 block">Processing Duration</span>
                <span className="font-semibold text-slate-800">
                  {selectedJobDetail.duration_seconds ? `${selectedJobDetail.duration_seconds.toFixed(2)} seconds` : "—"}
                </span>
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
              {(selectedJobDetail.failed_records > 0 || selectedJobDetail.duplicate_records > 0) && (
                <button
                  className="btn-outline text-xs py-1.5"
                  onClick={() => downloadImportErrors(selectedJobDetail.id, selectedJobDetail.filename)}
                >
                  Download Error CSV
                </button>
              )}
              <button
                className="btn-primary text-xs py-1.5"
                onClick={() => setSelectedJobDetail(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryStat({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-xs">
      <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wide">{label}</p>
      <p className={`mt-0.5 text-xl font-bold ${accent ?? "text-navy"}`}>{value.toLocaleString()}</p>
    </div>
  );
}

