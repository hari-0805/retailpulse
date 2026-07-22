import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { getDashboardSummary } from "../api/dashboard";
import { getSalesSummary } from "../api/sales";
import { listNotifications, markNotificationRead } from "../api/notifications";
import type { DashboardSummary, SalesDashboardSummary, AppNotification } from "../types";

function SummaryCard({ label, value, accent }: { label: string; value: string | number; accent: string }) {
  return (
    <div className="rounded-lg bg-white p-5 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className={`mt-1 text-3xl font-bold ${accent}`}>{value}</p>
    </div>
  );
}

function NotificationBell({ notifications, onMarkRead }: {
  notifications: AppNotification[];
  onMarkRead: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const unreadCount = notifications.filter((n) => !n.is_read).length;

  return (
    <div className="relative">
      <button
        className="relative rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"
        onClick={() => setOpen((o) => !o)}
      >
        🔔 Alerts
        {unreadCount > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-red-600 text-xs font-bold text-white">
            {unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 rounded-lg border border-slate-200 bg-white shadow-lg">
          <div className="max-h-96 overflow-y-auto p-2">
            {notifications.length === 0 && (
              <p className="p-3 text-sm text-slate-400">No stock alerts.</p>
            )}
            {notifications.map((n) => (
              <div
                key={n.id}
                className={`mb-1 rounded-md p-3 text-sm ${n.is_read ? "bg-white" : "bg-amber-50"}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className={n.type === "OUT_OF_STOCK" ? "text-red-700" : "text-amber-700"}>
                    {n.message}
                  </span>
                  {!n.is_read && (
                    <button
                      className="shrink-0 text-xs text-brand-500 hover:underline"
                      onClick={() => onMarkRead(n.id)}
                    >
                      Mark read
                    </button>
                  )}
                </div>
                <span className="mt-1 block text-xs text-slate-400">
                  {new Date(n.created_at).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [salesSummary, setSalesSummary] = useState<SalesDashboardSummary | null>(null);
  const [notifications, setNotifications] = useState<AppNotification[]>([]);

  const isAdmin = user?.role === "COMPANY_ADMIN" || user?.role === "SUPER_ADMIN";
  const isSalesUser = isAdmin || user?.role === "ANALYST";

  useEffect(() => {
    if (isAdmin) {
      getDashboardSummary().then(setSummary).catch(() => setSummary(null));
    }
    if (isSalesUser) {
      getSalesSummary().then(setSalesSummary).catch(() => setSalesSummary(null));
      listNotifications().then(setNotifications).catch(() => setNotifications([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const handleMarkRead = async (id: string) => {
    await markNotificationRead(id);
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, is_read: true } : n)));
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Welcome, {user?.name}</h1>
        <div className="flex items-center gap-3">
          {isSalesUser && <NotificationBell notifications={notifications} onMarkRead={handleMarkRead} />}
          <button className="btn-outline" onClick={handleLogout}>Logout</button>
        </div>
      </div>

      <div className="mb-6 rounded-lg bg-white p-6 shadow-sm">
        <p className="text-slate-800">
          You're signed in to <span className="font-semibold">{user?.company.name}</span> as{" "}
          <span className="font-semibold">{user?.role.replace("_", " ")}</span>.
        </p>
      </div>

      {isSalesUser && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <SummaryCard label="Total Units Sold" value={salesSummary?.total_sales ?? 0} accent="text-slate-900" />
            <SummaryCard label="Total Revenue" value={`₹${Number(salesSummary?.total_revenue ?? 0).toFixed(2)}`} accent="text-emerald-600" />
            <SummaryCard label="Total Orders" value={salesSummary?.total_orders ?? 0} accent="text-brand-600" />
            <SummaryCard label="Average Order Value" value={`₹${Number(salesSummary?.average_order_value ?? 0).toFixed(2)}`} accent="text-slate-900" />
          </div>
        </>
      )}

      {isAdmin && (
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard label="Total Products" value={summary?.total_products ?? 0} accent="text-slate-900" />
          <SummaryCard label="Active Products" value={summary?.active_products ?? 0} accent="text-emerald-600" />
          <SummaryCard label="Inactive Products" value={summary?.inactive_products ?? 0} accent="text-slate-500" />
          <SummaryCard label="Total Categories" value={summary?.total_categories ?? 0} accent="text-brand-600" />
        </div>
      )}

      {isSalesUser && (
        <div className="flex flex-wrap gap-3">
          <Link to="/sales" className="btn-primary">Manage Sales</Link>
          {isAdmin && <Link to="/products" className="btn-outline">Manage Products</Link>}
          {isAdmin && <Link to="/categories" className="btn-outline">Manage Categories</Link>}
        </div>
      )}
    </div>
  );
}
