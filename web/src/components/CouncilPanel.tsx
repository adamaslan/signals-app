/**
 * CouncilPanel — placeholder for the AI council panel.
 *
 * The full component depends on a RAG / multi-agent backend that is not yet
 * available. This renders a visible placeholder so the slot can be wired in
 * once the backend is ready.
 */
export function CouncilPanel() {
  return (
    <div className="rounded-xl border border-dashed border-white/10 bg-white/5 p-6 flex flex-col items-center justify-center gap-2 text-center">
      <span className="text-2xl">🤝</span>
      <p className="text-gray-400 text-sm font-medium">Council Panel</p>
      <p className="text-gray-600 text-xs max-w-xs">
        The AI analyst council will appear here once the RAG backend is wired up.
      </p>
    </div>
  );
}
