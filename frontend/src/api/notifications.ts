import { apiClient } from "./client";
import type { AppNotification } from "../types";

export const listNotifications = async (unreadOnly = false) => {
  const { data } = await apiClient.get<AppNotification[]>("/notifications", {
    params: { unread_only: unreadOnly },
  });
  return data;
};

export const markNotificationRead = async (id: string) => {
  const { data } = await apiClient.patch<AppNotification>(`/notifications/${id}/read`);
  return data;
};