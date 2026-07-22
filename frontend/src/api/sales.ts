import { apiClient } from "./client";
import type { Sale, SalePayload, SaleListResponse, SaleListParams, SalesDashboardSummary } from "../types";

export const listSales = async (params: SaleListParams) => {
  const { data } = await apiClient.get<SaleListResponse>("/sales", { params });
  return data;
};

export const getSale = async (id: string) => {
  const { data } = await apiClient.get<Sale>(`/sales/${id}`);
  return data;
};

export const createSale = async (payload: SalePayload) => {
  const { data } = await apiClient.post<Sale>("/sales", payload);
  return data;
};

export const updateSale = async (id: string, payload: Partial<SalePayload>) => {
  const { data } = await apiClient.put<Sale>(`/sales/${id}`, payload);
  return data;
};

export const deleteSale = async (id: string) => {
  await apiClient.delete(`/sales/${id}`);
};

export const getSalesSummary = async () => {
  const { data } = await apiClient.get<SalesDashboardSummary>("/sales/dashboard/summary");
  return data;
};