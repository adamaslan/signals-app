import { SettingsPanel } from "@/components/SettingsPanel";

export default function SettingsPage() {
  return (
    <div className="max-w-lg mx-auto space-y-6">
      <div>
        <a href="/" className="text-gray-500 hover:text-gray-300 text-sm">
          ← Home
        </a>
        <h1 className="text-3xl font-extrabold tracking-tight text-white mt-1">
          Settings
        </h1>
        <p className="text-gray-500 text-sm mt-1">
          Your profile and history live only on this device.
        </p>
      </div>
      <SettingsPanel />
    </div>
  );
}
