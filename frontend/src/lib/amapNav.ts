import type { NavLinks } from "../types";

// PROPOSAL §6.3: launching the AMap app from a web page.
//   * mobile browser  -> deep link (multi-waypoint), ~2s timeout -> web map
//   * WeChat          -> blocks custom schemes -> open web map + guidance
//   * desktop / no app -> open uri.amap.com web map
// The launch MUST be triggered by a user click (this fn is called from onClick).

const ua = () => navigator.userAgent.toLowerCase();
const isIOS = () => /iphone|ipad|ipod/.test(ua());
const isAndroid = () => /android/.test(ua());
const isWeChat = () => /micromessenger/.test(ua());
const isMobile = () => isIOS() || isAndroid();

export interface LaunchResult {
  mode: "ios" | "android" | "wechat" | "desktop" | "none";
  message?: string;
}

export function launchNav(nav: NavLinks): LaunchResult {
  if (!nav.web_uri && !nav.app_uri_android) {
    return { mode: "none", message: "这条行程还没有可用的导航坐标。" };
  }

  if (isWeChat()) {
    window.location.href = nav.web_uri;
    return {
      mode: "wechat",
      message: nav.web_supports_all_waypoints
        ? "微信里可能拦截唤起，请点右上角「在浏览器打开」后再试。"
        : "微信会拦截 App 深链；当前 Web 兜底只能带 1 个途经点，请在浏览器打开后使用完整 App 路线。",
    };
  }

  if (isMobile()) {
    const deep = isIOS() ? nav.app_uri_ios : nav.app_uri_android;
    const startedAt = Date.now();
    const timer = window.setTimeout(() => {
      // Still here after 2s -> app didn't open -> fall back to the web map.
      if (Date.now() - startedAt < 2500) window.location.href = nav.web_uri;
    }, 2000);
    const cancelOnLeave = () => {
      if (document.hidden) window.clearTimeout(timer);
    };
    document.addEventListener("visibilitychange", cancelOnLeave, { once: true });
    window.location.href = deep;
    return { mode: isIOS() ? "ios" : "android" };
  }

  window.open(nav.web_uri, "_blank", "noopener");
  return {
    mode: "desktop",
    message: nav.web_supports_all_waypoints
      ? "已在新标签打开高德网页地图（桌面端无 App）。"
      : "已打开高德网页地图；高德 Web URI 只能带 1 个途经点，完整多途经点请在手机高德 App 中打开，或按下方分段导航。",
  };
}
