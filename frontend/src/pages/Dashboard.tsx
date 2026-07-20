import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { getDashboardSummary } from "../api/dashboard";
import type { DashboardSummary } from "../types";

function SummaryCard({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div className="rounded-lg bg-white p-5 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className={`mt-1 text-3xl font-bold ${accent}`}>{value}</p>
    </div>
  );
}

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);

  useEffect(() => {
    getDashboardSummary().then(setSummary).catch(() => setSummary(null));
  }, []);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const isAdmin = user?.role === "COMPANY_ADMIN" || user?.role === "SUPER_ADMIN";

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Welcome, {user?.name}</h1>
        <button className="btn-outline" onClick={handleLogout}>Logout</button>
      </div>

      <div className="mb-6 rounded-lg bg-white p-6 shadow-sm">
        <p className="text-slate-800">
          You're signed in to <span className="font-semibold">{user?.company.name}</span> as{" "}
          <span className="font-semibold">{user?.role.replace("_", " ")}</span>.
        </p>
      </div>

      {isAdmin && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <SummaryCard label="Total Products" value={summary?.total_products ?? 0} accent="text-slate-900" />
            <SummaryCard label="Active Products" value={summary?.active_products ?? 0} accent="text-emerald-600" />
            <SummaryCard label="Inactive Products" value={summary?.inactive_products ?? 0} accent="text-slate-500" />
            <SummaryCard label="Total Categories" value={summary?.total_categories ?? 0} accent="text-brand-600" />
          </div>

          <div className="flex gap-3">
            <Link to="/products" className="btn-primary">Manage Products</Link>
            <Link to="/categories" className="btn-outline">Manage Categories</Link>
          </div>
        </>
      )}
    </div>
  );
}
