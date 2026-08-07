import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import type { UserRole } from "../types";

interface NavItem {
  to: string;
  label: string;
  icon: JSX.Element;
  roles?: UserRole[];
}

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5 shrink-0">
      <path d={d} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "Dashboard", icon: <Icon d="M3 10.5 12 3l9 7.5V21a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /> },
  {
    to: "/analytics", label: "Analytics",
    icon: <Icon d="M4 19V5m0 14h16M8 19v-6m4 6V9m4 10v-4" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN", "ANALYST"],
  },
  {
    to: "/customers", label: "Customers",
    icon: <Icon d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7 4h4m-2-2v4" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN", "ANALYST"],
  },
  {
    to: "/forecasting", label: "Demand Forecasting",
    icon: <Icon d="M3 17 9 11l4 4 8-8M15 7h6v6" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN", "ANALYST"],
  },
  {
    to: "/customer-analytics", label: "Customer Analytics",
    icon: <Icon d="M9 19v-6m4 6V9m4 10V5M5 19h14" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN", "ANALYST"],
  },
  {
    to: "/sales", label: "Sales",
    icon: <Icon d="M3 3h2l.4 2M7 13h10l3-8H5.4M7 13 5.4 5M7 13l-2.3 4.6A1 1 0 0 0 5.6 19H17M9 22a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm8 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN", "ANALYST"],
  },
  {
    to: "/inventory", label: "Inventory",
    icon: <Icon d="M3 7 12 3l9 4-9 4-9-4Zm0 0v10l9 4m0-14v14m9-14v10l-9 4" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN", "ANALYST"],
  },
  {
    to: "/products", label: "Products",
    icon: <Icon d="M20 7 12 3 4 7m16 0-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN"],
  },
  {
    to: "/categories", label: "Categories",
    icon: <Icon d="M4 4h7v7H4zm9 0h7v7h-7zm0 9h7v7h-7zM4 13h7v7H4z" />,
    roles: ["COMPANY_ADMIN", "SUPER_ADMIN"],
  },
  { to: "/profile", label: "Profile", icon: <Icon d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 9a7 7 0 0 1 14 0" /> },
];

export default function AppShell() {
  const { user, logout } = useAuth();

  const visibleItems = NAV_ITEMS.filter((item) => !item.roles || (user && item.roles.includes(user.role)));

  return (
    <div className="flex min-h-screen bg-slate-100">
      <aside className="fixed inset-y-0 left-0 flex w-60 flex-col border-r border-slate-200 bg-white">
        <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-500 text-sm font-bold text-white">
            R
          </div>
          <span className="text-base font-semibold text-slate-800">RetailPulse</span>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
          {visibleItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-brand-50 text-brand-700"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-slate-200 p-3">
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700">
              {user?.name?.[0]?.toUpperCase() ?? "?"}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-800">{user?.name}</p>
              <p className="truncate text-xs text-slate-500">{user?.role.replace("_", " ")}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => logout()}
            className="mt-1 flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900"
          >
            <Icon d="M9 21H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h4m5 14 5-5-5-5m5 5H9" />
            Log out
          </button>
        </div>
      </aside>

      <div className="ml-60 flex min-h-screen w-full flex-col">
        <main className="flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
