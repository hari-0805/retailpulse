import { apiClient } from "./client";
import type {
  ImportType, ImportUploadResponse, ImportJobListResponse, ImportResult, ImportRowIssue,
  ImportProgress, ImportJob, DuplicateStrategy,
} from "../types";

export const downloadImportTemplate = async (importType: ImportType) => {
  const response = await apiClient.get(`/import/template/${importType}`, {
    responseType: "blob",
  });
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", `${importType.toLowerCase()}_template.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

export const uploadImport = async (importType: ImportType, file: File) => {
  const formData = new FormData();
  formData.append("import_type", importType);
  formData.append("file", file);
  const { data } = await apiClient.post<ImportUploadResponse>("/import/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
};

export const revalidateImport = async (importId: string) => {
  const { data } = await apiClient.post<ImportUploadResponse>(`/import/${importId}/validate`);
  return data;
};

export const processImport = async (importId: string, duplicateStrategy: DuplicateStrategy = "SKIP") => {
  const { data } = await apiClient.post<ImportResult>(`/import/${importId}/process`, {
    duplicate_strategy: duplicateStrategy,
  });
  return data;
};

export const getImportStatus = async (importId: string) => {
  const { data } = await apiClient.get<ImportProgress>(`/import/${importId}/status`);
  return data;
};

export const cancelImport = async (importId: string) => {
  const { data } = await apiClient.post<ImportJob>(`/import/${importId}/cancel`);
  return data;
};

export const getImportDetail = async (importId: string) => {
  const { data } = await apiClient.get<ImportJob>(`/import/${importId}`);
  return data;
};

export const listImportHistory = async (importType?: ImportType) => {
  const { data } = await apiClient.get<ImportJobListResponse>("/import/history", {
    params: importType ? { import_type: importType } : undefined,
  });
  return data;
};

export const getImportErrors = async (importId: string) => {
  const { data } = await apiClient.get<ImportRowIssue[]>(`/import/${importId}/errors`);
  return data;
};

export const downloadImportErrors = async (importId: string, filename: string) => {
  const response = await apiClient.get(`/import/${importId}/errors`, {
    params: { format: "csv" },
    responseType: "blob",
  });
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement("a");
  link.href = url;
  const baseName = filename.replace(/\.csv$/i, "");
  link.setAttribute("download", `${baseName}_errors.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

