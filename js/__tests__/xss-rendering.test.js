import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadScript } from "./helpers/load-script.js";

globalThis.ApiClient = {
  get: vi.fn(),
  post: vi.fn(),
};

loadScript("js/utils.js");
loadScript("js/app.js");
loadScript("js/notifications.js");

beforeEach(() => {
  document.body.innerHTML = '<div id="toast-container"></div>';
  ApiClient.get.mockReset();
  ApiClient.post.mockReset();
  ApiClient.post.mockResolvedValue({ unread: 0 });
});

afterEach(() => {
  Notifications.stop();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("notification output and navigation boundary", () => {
  it("renders persisted fields as text and delegates clicks through an in-memory record", async () => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div class="header-right"><div class="avatar"></div></div>';
    ApiClient.get.mockImplementation(async (path) => {
      if (path === "/api/auth/me") return { user: { id: 1, role: "admin" } };
      if (path === "/api/notifications") return { unread: 1, notifications: [] };
      return { unread: 1 };
    });
    await Notifications.init();

    const payload = '<img src=x onerror="window.__notifPwned = true">';
    const unsafeLink = "javascript:window.__notifPwned=true";
    Notifications.renderNotifications([{
      id: "7' onclick='window.__notifPwned=true",
      type: "success\" onmouseover=\"window.__notifPwned=true",
      title: payload,
      body: payload,
      link: unsafeLink,
      created_at: "2026-08-22T20:00:00",
      is_read: false,
    }]);

    const item = document.querySelector(".notif-item");
    expect(item.hasAttribute("onclick")).toBe(false);
    expect(item.querySelector(".notif-item-title").textContent).toBe(payload);
    expect(item.querySelector(".notif-item-body-text").textContent).toBe(payload);
    expect(item.querySelector("img, script")).toBeNull();

    const navigate = vi.spyOn(Ui, "safeNavigate").mockReturnValue(false);
    item.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
    vi.advanceTimersByTime(150);

    expect(ApiClient.post).toHaveBeenCalledWith("/api/notifications/read", {
      id: "7' onclick='window.__notifPwned=true",
    });
    expect(navigate).toHaveBeenCalledWith(unsafeLink);
  });
});

describe("showToast output boundary", () => {
  it.each([
    '<img src=x onerror="window.__toastPwned = true">',
    '</div><script>window.__toastPwned = true</script>',
    '\"><svg onload="window.__toastPwned = true">',
  ])("renders attacker title and message as inert text: %s", (payload) => {
    window.__toastPwned = false;

    showToast("error", payload, payload);

    const toast = document.querySelector(".toast");
    expect(toast.querySelector(".toast-title").textContent).toBe(payload);
    expect(toast.querySelector(".toast-msg").textContent).toBe(payload);
    expect(toast.querySelector("img, script, .toast-body svg")).toBeNull();
    expect(window.__toastPwned).toBe(false);
  });
});
