/**
 * Lexi YouTube Bridge — Scoped YouTube Content Script
 *
 * Safety rules enforced:
 * - Runs only on https://www.youtube.com/* and https://youtu.be/*.
 * - Treats all page text (titles, labels) strictly as untrusted data.
 * - Never auto-approves permissions, consent dialogs, logins, payments, or account prompts.
 * - Rejects any message containing arbitrary JavaScript or unknown stages.
 * - Reports truthful verification flags (search_verified, watch_verified, playing_verified, fullscreen_verified).
 */

"use strict";

const ALLOWED_STAGES = new Set(["search", "verify_watch", "playback_and_fullscreen"]);
const ALLOWED_STAGE_KEYS = new Set(["type", "stage", "query", "expected_url"]);
const FORBIDDEN_STAGE_KEYS = new Set([
  "js",
  "javascript",
  "script",
  "code",
  "eval",
  "function",
  "html",
  "shell",
  "cmd"
]);

function sanitizePageText(raw, maxLen = 200) {
  if (typeof raw !== "string") {
    return "";
  }
  return raw
    .replace(/[\x00-\x1f\x7f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLen);
}

function validateStageMessage(msg) {
  if (!msg || typeof msg !== "object" || Array.isArray(msg)) {
    return { valid: false, reason: "Invalid stage message object" };
  }
  for (const k of Object.keys(msg)) {
    if (FORBIDDEN_STAGE_KEYS.has(k.toLowerCase())) {
      return { valid: false, reason: `Forbidden field '${k}' rejected by content script` };
    }
    if (!ALLOWED_STAGE_KEYS.has(k)) {
      return { valid: false, reason: `Unexpected field '${k}' rejected by content script` };
    }
  }
  if (msg.type !== "lexi_stage") {
    return { valid: false, reason: "Invalid message type" };
  }
  if (typeof msg.stage !== "string" || !ALLOWED_STAGES.has(msg.stage)) {
    return { valid: false, reason: `Unsupported stage: '${String(msg.stage)}'` };
  }
  return { valid: true };
}

/**
 * Detect if YouTube is showing a sign-in wall, consent modal, age gate, or payment dialog.
 * Never clicks or auto-approves any such prompt.
 */
function detectPageBlocker(doc) {
  const modalSelectors = [
    "ytd-consent-bump-v2-lightbox",
    "tp-yt-paper-dialog[aria-modal='true']",
    "ytd-enforcement-message-view-model",
    "yt-playability-error-supported-renderers"
  ];
  for (const sel of modalSelectors) {
    const el = doc.querySelector(sel);
    if (el && !el.hasAttribute("hidden") && el.style.display !== "none") {
      const txt = sanitizePageText(el.textContent || "", 120).toLowerCase();
      if (
        txt.includes("sign in") ||
        txt.includes("before you continue") ||
        txt.includes("confirm your age") ||
        txt.includes("purchase") ||
        txt.includes("payment")
      ) {
        return "YouTube login, consent, or age/payment prompt is blocking the page";
      }
    }
  }
  return null;
}

function normalizeWatchUrl(href, baseOrigin = "https://www.youtube.com") {
  if (typeof href !== "string" || !href.trim()) {
    return null;
  }
  try {
    const u = new URL(href, baseOrigin);
    if (u.protocol !== "https:") {
      return null;
    }
    if (
      (u.hostname === "www.youtube.com" || u.hostname === "youtube.com") &&
      u.pathname === "/watch" &&
      u.searchParams.get("v")
    ) {
      const videoId = u.searchParams.get("v");
      return `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`;
    }
    if (u.hostname === "youtu.be" && u.pathname.length > 1) {
      return `https://www.youtube.com/watch?v=${encodeURIComponent(u.pathname.slice(1))}`;
    }
    return null;
  } catch (_err) {
    return null;
  }
}

function isAdOrShortsElement(anchor) {
  if (!anchor) {
    return true;
  }
  const href = anchor.getAttribute ? anchor.getAttribute("href") || "" : anchor.href || "";
  if (href.includes("/shorts/") || href.includes("googleadservices")) {
    return true;
  }
  if (typeof anchor.closest === "function") {
    const adAncestor = anchor.closest(
      "ytd-ad-slot-renderer, ytd-promoted-video-renderer, ytd-promoted-sparkles-web-renderer, ytd-in-feed-ad-layout-renderer, ytd-reel-shelf-renderer"
    );
    if (adAncestor) {
      return true;
    }
  }
  return false;
}

function findFirstVideoResult(doc) {
  const candidateSelectors = [
    "ytd-video-renderer a#video-title",
    "ytd-video-renderer a#video-title-link",
    "ytd-rich-item-renderer a#video-title-link",
    "ytd-rich-item-renderer a#video-title",
    "a#video-title[href*='/watch?v=']"
  ];

  for (const selector of candidateSelectors) {
    const anchors = Array.from(doc.querySelectorAll(selector));
    for (const anchor of anchors) {
      if (isAdOrShortsElement(anchor)) {
        continue;
      }
      const rawHref =
        (anchor.getAttribute && anchor.getAttribute("href")) || anchor.href || "";
      const watchUrl = normalizeWatchUrl(rawHref);
      if (!watchUrl) {
        continue;
      }
      const rawTitle =
        (anchor.getAttribute && anchor.getAttribute("title")) ||
        anchor.textContent ||
        "";
      const title = sanitizePageText(rawTitle) || "YouTube Video";
      return {
        video_url: watchUrl,
        title,
        element: anchor
      };
    }
  }
  return null;
}

async function pollUntil(fn, timeoutMs = 4500, intervalMs = 150) {
  const deadline = Date.now() + timeoutMs;
  while (true) {
    const res = await fn();
    if (res) {
      return res;
    }
    if (Date.now() >= deadline) {
      return null;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/**
 * Stage 1: Verify YouTube search results page and pick the first valid video result.
 */
async function handleSearchStage(doc, options = {}) {
  // A completed navigation does not mean YouTube's dynamic result cards exist.
  // Polling is bounded and only runs during a requested browser action.
  const timeoutMs = typeof options.timeoutMs === "number" ? options.timeoutMs : 12000;
  const intervalMs = typeof options.intervalMs === "number" ? options.intervalMs : 150;

  const blocker = detectPageBlocker(doc);
  if (blocker) {
    return {
      ok: false,
      no_retry: true,
      search_verified: false,
      video_url: "",
      title: "",
      reason: blocker
    };
  }

  const found = await pollUntil(() => {
    const noResultsEl = doc.querySelector(
      "ytd-background-promo-renderer, .promo-title.ytd-background-promo-renderer"
    );
    const match = findFirstVideoResult(doc);
    if (match) {
      return { status: "found", match };
    }
    if (noResultsEl) {
      const promoText = sanitizePageText(noResultsEl.textContent || "").toLowerCase();
      if (promoText.includes("no results found") || promoText.includes("try different keywords")) {
        return { status: "empty" };
      }
    }
    return null;
  }, timeoutMs, intervalMs);

  if (!found || found.status !== "found" || !found.match) {
    return {
      ok: false,
      search_verified: false,
      video_url: "",
      title: "",
      reason: "No matching YouTube video results found on search page"
    };
  }

  return {
    ok: true,
    search_verified: true,
    video_url: found.match.video_url,
    title: found.match.title,
    reason: "Selected YouTube video result"
  };
}

/**
 * Stage 2: Verify the YouTube watch page and <video> element are present.
 */
async function handleVerifyWatchStage(doc, options = {}) {
  const timeoutMs = typeof options.timeoutMs === "number" ? options.timeoutMs : 4500;
  const intervalMs = typeof options.intervalMs === "number" ? options.intervalMs : 150;

  const blocker = detectPageBlocker(doc);
  if (blocker) {
    return {
      ok: false,
      no_retry: true,
      watch_verified: false,
      title: "",
      url: doc.location ? doc.location.href : "",
      reason: blocker
    };
  }

  const watchState = await pollUntil(() => {
    const loc = doc.location || {};
    const href = loc.href || "";
    const pathname = loc.pathname || "";
    const isWatch =
      pathname === "/watch" ||
      href.includes("youtube.com/watch") ||
      href.includes("youtu.be/");
    if (!isWatch) {
      return null;
    }
    const videoEl =
      doc.querySelector("video.html5-main-video") || doc.querySelector("video");
    if (!videoEl) {
      return null;
    }
    const headingEl =
      doc.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
      doc.querySelector("ytd-watch-metadata h1") ||
      doc.querySelector("#title h1");
    const rawTitle =
      (headingEl && headingEl.textContent) ||
      (doc.title ? doc.title.replace(/\s*-\s*YouTube\s*$/i, "") : "");
    return {
      videoEl,
      title: sanitizePageText(rawTitle),
      url: normalizeWatchUrl(href) || href
    };
  }, timeoutMs, intervalMs);

  if (!watchState) {
    return {
      ok: false,
      watch_verified: false,
      title: "",
      url: doc.location ? doc.location.href : "",
      reason: "Could not verify YouTube watch page and video element"
    };
  }

  return {
    ok: true,
    watch_verified: true,
    title: watchState.title,
    url: watchState.url,
    reason: "Watch page and video element verified"
  };
}

/**
 * Stage 3: Attempt playback and fullscreen safely when browser policy allows it.
 * Never claims playback or fullscreen unless verified via DOM state.
 */
async function handlePlaybackAndFullscreenStage(doc, win = null) {
  const activeWin = win || (typeof window !== "undefined" ? window : {});
  const blocker = detectPageBlocker(doc);
  if (blocker) {
    return {
      ok: false,
      no_retry: true,
      playing_verified: false,
      fullscreen_verified: false,
      title: "",
      url: doc.location ? doc.location.href : "",
      reason: blocker
    };
  }

  const videoEl =
    doc.querySelector("video.html5-main-video") || doc.querySelector("video");
  if (!videoEl) {
    return {
      ok: false,
      playing_verified: false,
      fullscreen_verified: false,
      title: "",
      url: doc.location ? doc.location.href : "",
      reason: "Video element not found on YouTube watch page"
    };
  }

  const headingEl =
    doc.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
    doc.querySelector("ytd-watch-metadata h1") ||
    doc.querySelector("#title h1");
  const title = sanitizePageText(
    (headingEl && headingEl.textContent) ||
      (doc.title ? doc.title.replace(/\s*-\s*YouTube\s*$/i, "") : "")
  );
  const currentUrl =
    (doc.location && normalizeWatchUrl(doc.location.href)) ||
    (doc.location ? doc.location.href : "");

  // 1. Playback attempt & verification
  let playingVerified = Boolean(!videoEl.paused && !videoEl.ended);
  let playbackReason = "";

  if (!playingVerified && typeof videoEl.play === "function") {
    try {
      const playPromise = videoEl.play();
      if (playPromise && typeof playPromise.then === "function") {
        await playPromise;
      }
      playingVerified = Boolean(!videoEl.paused && !videoEl.ended);
      if (!playingVerified) {
        playbackReason = "Browser policy kept video paused (user gesture required for playback)";
      }
    } catch (err) {
      playingVerified = false;
      const errName = err && err.name ? err.name : "";
      if (errName === "NotAllowedError" || /gesture|interact|unmuted/i.test(String(err))) {
        playbackReason = "Chrome required a user gesture to start playback";
      } else {
        playbackReason = `Playback could not start automatically (${sanitizePageText(String(err.message || err), 80)})`;
      }
    }
  }

  // 2. Fullscreen attempt & verification
  let fullscreenVerified = Boolean(
    doc.fullscreenElement || doc.webkitFullscreenElement
  );
  let fullscreenReason = "";

  if (!fullscreenVerified) {
    const playerContainer =
      doc.querySelector("#movie_player") ||
      doc.querySelector(".html5-video-player") ||
      videoEl;
    const fsButton = doc.querySelector("button.ytp-fullscreen-button");
    const hasUserActivation = Boolean(
      activeWin.navigator &&
        activeWin.navigator.userActivation &&
        activeWin.navigator.userActivation.isActive
    );

    let fsError = null;
    if (hasUserActivation && fsButton && typeof fsButton.click === "function") {
      try {
        fsButton.click();
      } catch (err) {
        fsError = err;
      }
    }

    fullscreenVerified = Boolean(
      doc.fullscreenElement || doc.webkitFullscreenElement
    );

    if (!fullscreenVerified && playerContainer) {
      const reqFs =
        playerContainer.requestFullscreen ||
        playerContainer.webkitRequestFullscreen;
      if (typeof reqFs === "function") {
        try {
          const fsPromise = reqFs.call(playerContainer);
          if (fsPromise && typeof fsPromise.then === "function") {
            await fsPromise;
          }
        } catch (err) {
          fsError = err;
        }
      }
    }

    fullscreenVerified = Boolean(
      doc.fullscreenElement || doc.webkitFullscreenElement
    );

    if (!fullscreenVerified) {
      const errText = fsError ? String(fsError.message || fsError) : "";
      if (
        !hasUserActivation ||
        (fsError && fsError.name === "NotAllowedError") ||
        /gesture|activation|permission/i.test(errText)
      ) {
        fullscreenReason =
          "Chrome required a user gesture for fullscreen; click the fullscreen button or press 'f' once in the YouTube tab";
      } else {
        fullscreenReason =
          "Fullscreen was blocked by browser policy; one click or 'f' key from the user is required";
      }
    }
  }

  const reasons = [];
  if (!playingVerified && playbackReason) {
    reasons.push(playbackReason);
  }
  if (!fullscreenVerified && fullscreenReason) {
    reasons.push(fullscreenReason);
  }

  return {
    ok: true,
    // If fullscreen or playback failed due to user gesture policy, retrying without user gesture won't change policy
    no_retry: true,
    playing_verified: playingVerified,
    fullscreen_verified: fullscreenVerified,
    title,
    url: currentUrl,
    reason: reasons.join("; ")
  };
}

async function dispatchStageMessage(msg, doc = null, win = null) {
  const check = validateStageMessage(msg);
  if (!check.valid) {
    return {
      ok: false,
      no_retry: true,
      rejected: true,
      reason: check.reason
    };
  }
  const activeDoc = doc || (typeof document !== "undefined" ? document : null);
  const activeWin = win || (typeof window !== "undefined" ? window : null);

  if (!activeDoc) {
    return {
      ok: false,
      no_retry: true,
      reason: "Document context unavailable"
    };
  }

  if (msg.stage === "search") {
    return await handleSearchStage(activeDoc);
  }
  if (msg.stage === "verify_watch") {
    return await handleVerifyWatchStage(activeDoc);
  }
  if (msg.stage === "playback_and_fullscreen") {
    return await handlePlaybackAndFullscreenStage(activeDoc, activeWin);
  }
  return {
    ok: false,
    no_retry: true,
    reason: `Unhandled stage: ${msg.stage}`
  };
}

if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
  try {
    chrome.runtime.sendMessage({ type: "lexi_content_ready" }, () => {
      void chrome.runtime.lastError;
    });
  } catch (_e) {}

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.type !== "lexi_stage") {
      return false;
    }
    dispatchStageMessage(msg)
      .then((res) => sendResponse(res))
      .catch((err) =>
        sendResponse({
          ok: false,
          no_retry: true,
          reason: err && err.message ? err.message : "Content script stage error"
        })
      );
    return true; // keep sendResponse channel open for async completion
  });
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    sanitizePageText,
    validateStageMessage,
    detectPageBlocker,
    normalizeWatchUrl,
    findFirstVideoResult,
    handleSearchStage,
    handleVerifyWatchStage,
    handlePlaybackAndFullscreenStage,
    dispatchStageMessage
  };
}
