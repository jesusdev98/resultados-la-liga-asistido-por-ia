// A legacy MCP-UI embedded resource, transported by the local HTTP adapter.
// An opaque-origin frame may report its height, never request host actions.
export function resizeMcpResource(event, frames = document.querySelectorAll(".mcp-ui-frame")) {
  const data = event.data;
  if (event.origin !== "null" || data?.type !== "laliga-panel:resize" ||
      !Number.isInteger(data.height) || data.height < 1 || data.height > 8192) return;
  for (const frame of frames) {
    if (event.source !== frame.contentWindow) continue;
    // The initial CSS height is 220px. A too-early child measurement must not
    // collapse the card and clip its date/crests; later measurements may expand it.
    const current = Number.parseInt(frame.style.height, 10) || 220;
    frame.style.height = `${Math.min(8192, Math.max(220, current, data.height))}px`;
  }
}

if (typeof window !== "undefined") window.addEventListener("message", (event) => resizeMcpResource(event));

export function appendMcpResources(container, content) {
  let count = 0;
  for (const block of Array.isArray(content) ? content : []) {
    const resource = block?.resource;
    if (block?.type !== "resource" || resource?.uri !== "ui://prueba-deepseek/panel" ||
        resource?.mimeType !== "text/html" || typeof resource?.text !== "string" ||
        resource.text.length > 64 * 1024) continue;
    const frame = document.createElement("iframe");
    frame.className = "mcp-ui-frame";
    frame.title = "Panel MCP-UI de La Liga · datos verificados de ESPN";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.referrerPolicy = "no-referrer";
    frame.srcdoc = resource.text;
    container.appendChild(frame);
    count += 1;
    break;
  }
  return count;
}
