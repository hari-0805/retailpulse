import { apiClient } from "./client";
import type {
  DQSummary, DQRun, DQRunListResponse, DQIssueListResponse, DQIssueDetail, DQIssue, DQIssueFilters, IssueStatus,
} from "../types";

export const getDQSummary = async () => {
  const { data } = await apiClient.get<DQSummary>("/data-quality/summary");
  return data;
};

export const startReconciliation = async () => {
  const { data } = await apiClient.post<DQRun>("/data-quality/runs");
  return data;
};

export const getReconciliationRun = async (id: string) => {
  const { data } = await apiClient.get<DQRun>(`/data-quality/runs/${id}`);
  return data;
};

export const listReconciliationRuns = async (page = 1, pageSize = 20) => {
  const { data } = await apiClient.get<DQRunListResponse>("/data-quality/runs", { params: { page, page_size: pageSize } });
  return data;
};

export const listDQIssues = async (filters: DQIssueFilters) => {
  const { data } = await apiClient.get<DQIssueListResponse>("/data-quality/issues", { params: filters });
  return data;
};

export const getDQIssueFilterOptions = async () => {
  const { data } = await apiClient.get<{ issue_types: string[]; modules: string[] }>("/data-quality/issues/filter-options");
  return data;
};

export const getDQIssue = async (id: string) => {
  const { data } = await apiClient.get<DQIssueDetail>(`/data-quality/issues/${id}`);
  return data;
};

export const updateDQIssueStatus = async (id: string, status: IssueStatus, resolution_note?: string) => {
  const { data } = await apiClient.patch<DQIssue>(`/data-quality/issues/${id}/status`, { status, resolution_note });
  return data;
};
