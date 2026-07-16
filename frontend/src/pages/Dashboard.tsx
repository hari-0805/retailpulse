import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Welcome, {user?.name}</h1>
        <button className="btn-outline" onClick={handleLogout}>Logout</button>
      </div>

      <div className="rounded-lg bg-white p-6 shadow-sm">
        <p className="text-slate-800">
          You're signed in to <span className="font-semibold">{user?.company.name}</span> as{" "}
          <span className="font-semibold">{user?.role.replace("_", " ")}</span>.
        </p>
        <p className="mt-2 text-sm text-slate-500">
          Charts, products, sales and inventory widgets will be built in the modules that follow.
        </p>
      </div>
    </div>
  );
}
