export default function RefreshButton() {
  return (
    <button
      type="button"
      aria-label="Refresh tab"
      title="Refresh tab"
      onClick={() => window.location.reload()}
      style={{
        display: "grid",
        placeItems: "center",
        flex: "0 0 auto",
        width: 28,
        height: 28,
        padding: 0,
        color: "inherit",
        background: "rgba(255,255,255,.08)",
        border: "1px solid rgba(255,255,255,.18)",
        borderRadius: 8,
        cursor: "pointer",
      }}
    >
      <svg
        aria-hidden="true"
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M20 7v5h-5" />
        <path d="M4 17v-5h5" />
        <path d="M5.6 9a7 7 0 0 1 11.6-2L20 12" />
        <path d="M18.4 15a7 7 0 0 1-11.6 2L4 12" />
      </svg>
    </button>
  );
}
