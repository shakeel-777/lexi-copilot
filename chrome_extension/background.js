/**
 * Lexi YouTube Bridge — Manifest V3 Background Service Worker
 *
 * Safety & Architecture Guarantees:
 * - Scoped strictly to YouTube (https://www.youtube.com/* and https://youtu.be/*) for
 *   automation, plus ONE narrow tab-closing command (close_named_tabs) limited to a
 *   fixed enum of sites (whatsapp, instagram) with exact, hard-coded URL patterns.
 * - Communicates with Lexi exclusively via Chrome Native Messaging ("com.lexi.browser_bridge").
 * - Rejects unknown commands, malformed payloads, unexpected fields, and any JavaScript/code payloads.
 * - Executes a bounded 3-stage workflow:
 *     Stage 1: search
 *     Stage 2: open_watch
 *     Stage 3: playback_and_fullscreen
 * - Explicit timeout per stage and at most 1 retry per stage.
 */

"use strict";

const NATIVE_HOST_NAME = "com.lexi.browser_bridge";
const YOUTUBE_COMMAND = "youtube_play_fullscreen";
const CLOSE_TABS_COMMAND = "close_named_tabs";
const ALLOWED_COMMANDS = new Set([YOUTUBE_COMMAND, CLOSE_TABS_COMMAND]);
const ALLOWED_REQUEST_KEYS = new Set(["command", "query", "target", "request_id"]);

// The ONLY tabs close_named_tabs may ever touch.  Patterns are hard-coded here;
// nothing from Lexi (or a web page) can add to or alter this table.
const CLOSE_TARGET_SITES = {
  whatsapp: {
    hosts: ["web.whatsapp.com"],
    patterns: ["https://web.whatsapp.com/*"]
  },
  instagram: {
    hosts: ["www.instagram.com", "instagram.com"],
    patterns: ["https://www.instagram.com/*", "https://instagram.com/*"]
  }
};
const CLOSE_TARGETS = new Set([...Object.keys(CLOSE_TARGET_SITES), "both"]);
const CLOSE_TIMEOUT_MS = 8000;
const MAX_CLOSE_TABS_PER_SITE = 50;
const FORBIDDEN_KEYS = new Set([
  "js",
  "javascript",
  "script",
  "code",
  "eval",
  "function",
  "fn",
  "shell",
  "cmd",
  "exec",
  "html",
  "url",
  "href"
]);

const FORBIDDEN_QUERY_RE =
  /(?:javascript\s*:|data\s*:\s*text\/html|<\s*\/?\s*script\b|\beval\s*\(|\bnew\s+Function\b|\bFunction\s*\(|\bdocument\s*\.|\bwindow\s*\.|\bchrome\s*\.|\bglobalThis\s*\.|=>|[;{}])/i;

const CONTROL_CHAR_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/;

// YouTube commonly reports a completed tab before its search-result cards have
// rendered.  Give the content script a realistic bounded window to observe them.
const STAGE_TIMEOUT_MS = 16000;
const MAX_STAGE_RETRIES = 1; // At most 1 retry per stage (max 2 attempts total)

function sanitizeUntrustedText(raw, maxLen = 200) {
  if (typeof raw !== "string") {
    return "";
  }
  return raw
    .replace(/[\x00-\x1f\x7f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLen);
}

function isAllowedYouTubeWatchUrl(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) {
    return false;
  }
  try {
    const u = new URL(rawUrl);
    if (u.protocol !== "https:") {
      return false;
    }
    if (u.hostname === "www.youtube.com" || u.hostname === "youtube.com") {
      return u.pathname === "/watch" && Boolean(u.searchParams.get("v"));
    }
    if (u.hostname === "youtu.be") {
      return u.pathname.length > 1;
    }
    return false;
  } catch (_err) {
    return false;
  }
}

/**
 * Validate a native messaging request from Lexi.
 * Rejects unknown commands, malformed objects, unexpected keys, and any JS/script injection.
 */
function validateBridgeRequest(msg) {
  if (!msg || typeof msg !== "object" || Array.isArray(msg)) {
    return {
      valid: false,
      reason: "Malformed payload: request must be a JSON object"
    };
  }

  const keys = Object.keys(msg);
  for (const key of keys) {
    if (FORBIDDEN_KEYS.has(key.toLowerCase())) {
      return {
        valid: false,
        reason: `Rejected payload: forbidden executable field '${key}' is not permitted`
      };
    }
    if (!ALLOWED_REQUEST_KEYS.has(key)) {
      return {
        valid: false,
        reason: `Malformed payload: unexpected field '${key}'`
      };
    }
  }

  if (typeof msg.command !== "string" || !msg.command.trim()) {
    return {
      valid: false,
      reason: "Malformed payload: missing string 'command'"
    };
  }

  const command = msg.command.trim();
  if (!ALLOWED_COMMANDS.has(command)) {
    return {
      valid: false,
      reason: `Unsupported command: '${command}'`
    };
  }

  if (command === CLOSE_TABS_COMMAND) {
    if (msg.query !== undefined) {
      return { valid: false, reason: "Malformed payload: unexpected field 'query'" };
    }
    const target = typeof msg.target === "string" ? msg.target.trim().toLowerCase() : "";
    if (!CLOSE_TARGETS.has(target)) {
      return {
        valid: false,
        reason: `Malformed payload: 'target' must be one of ${[...CLOSE_TARGETS].sort().join(", ")}`
      };
    }
    if (msg.request_id !== undefined && msg.request_id !== null) {
      if (typeof msg.request_id !== "string" || !/^[a-zA-Z0-9_.-]{1,64}$/.test(msg.request_id)) {
        return { valid: false, reason: "Malformed payload: invalid 'request_id'" };
      }
    }
    return { valid: true, command, target, request_id: msg.request_id || null };
  }

  if (msg.target !== undefined) {
    return { valid: false, reason: "Malformed payload: unexpected field 'target'" };
  }

  if (typeof msg.query !== "string") {
    return {
      valid: false,
      reason: "Malformed payload: 'query' must be a string"
    };
  }

  const query = msg.query.trim();
  if (!query || query.length > 200) {
    return {
      valid: false,
      reason: "Malformed payload: 'query' must be between 1 and 200 characters"
    };
  }

  if (CONTROL_CHAR_RE.test(query)) {
    return {
      valid: false,
      reason: "Malformed payload: 'query' contains control characters"
    };
  }

  if (FORBIDDEN_QUERY_RE.test(query)) {
    return {
      valid: false,
      reason: "Rejected payload: JavaScript or code patterns are not allowed in 'query'"
    };
  }

  if (msg.request_id !== undefined && msg.request_id !== null) {
    if (
      typeof msg.request_id !== "string" ||
      !/^[a-zA-Z0-9_.-]{1,64}$/.test(msg.request_id)
    ) {
      return {
        valid: false,
        reason: "Malformed payload: invalid 'request_id'"
      };
    }
  }

  return {
    valid: true,
    command,
    query,
    request_id: msg.request_id || null
  };
}

function withTimeout(promise, timeoutMs, timeoutReason) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(timeoutReason || `Stage timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    Promise.resolve(promise)
      .then((val) => {
        clearTimeout(timer);
        resolve(val);
      })
      .catch((err) => {
        clearTimeout(timer);
        reject(err);
      });
  });
}

async function runStageWithRetry(stageName, stageFn, maxRetries = MAX_STAGE_RETRIES, timeoutMs = STAGE_TIMEOUT_MS) {
  let attempts = 0;
  let lastError = null;
  let lastResult = null;

  while (attempts <= maxRetries) {
    attempts += 1;
    try {
      const res = await withTimeout(
        stageFn(attempts),
        timeoutMs,
        `Stage '${stageName}' timed out after ${timeoutMs}ms`
      );
      lastResult = res;
      if (res && res.ok) {
        return { ...res, attempts, stage: stageName };
      }
      if (res && res.no_retry) {
        return { ...res, attempts, stage: stageName };
      }
    } catch (err) {
      lastError = err;
    }
  }

  return {
    ok: false,
    attempts,
    stage: stageName,
     ...(lastResult || {}),
    reason:
      (lastResult && lastResult.reason) ||
      (lastError && lastError.message) ||
      `Stage '${stageName}' failed after ${attempts} attempt(s)`
  };
}

/**
 * Execute the 3-stage youtube_play_fullscreen workflow:
 *   1. search
 *   2. open_watch
 *   3. playback_and_fullscreen
 *
 * Accepts an optional `driver` for deterministic local testing.
 */
async function executeYouTubePlayFullscreen(rawRequest, driver = null) {
  const validation = validateBridgeRequest(rawRequest);
  const requestId =
    rawRequest && typeof rawRequest === "object" && typeof rawRequest.request_id === "string"
      ? rawRequest.request_id
      : null;

  if (!validation.valid) {
    return {
      request_id: requestId,
      success: false,
      rejected: true,
      search_verified: false,
      watch_verified: false,
      playing_verified: false,
      fullscreen_verified: false,
      title: "",
      url: "",
      reason: validation.reason
    };
  }

  if (validation.command !== YOUTUBE_COMMAND) {
    return {
      request_id: validation.request_id,
      success: false,
      rejected: true,
      search_verified: false,
      watch_verified: false,
      playing_verified: false,
      fullscreen_verified: false,
      title: "",
      url: "",
      reason: `Unsupported command for the YouTube workflow: '${validation.command}'`
    };
  }

  const activeDriver = driver || createChromeTabDriver();
  const query = validation.query;
  const searchUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`;
  const maxRetries =
    activeDriver && typeof activeDriver.maxRetries === "number"
      ? Math.min(activeDriver.maxRetries, MAX_STAGE_RETRIES)
      : MAX_STAGE_RETRIES;
  const stageTimeoutMs =
    activeDriver && typeof activeDriver.stageTimeoutMs === "number"
      ? activeDriver.stageTimeoutMs
      : STAGE_TIMEOUT_MS;

  // Stage 1: Search YouTube and select a suitable video result
  const stage1 = await runStageWithRetry(
    "search",
    async (attempt) => {
      return await activeDriver.runSearchStage({
        query,
        searchUrl,
        attempt
      });
    },
    maxRetries,
    stageTimeoutMs
  );

  if (!stage1.ok || !stage1.search_verified || !isAllowedYouTubeWatchUrl(stage1.video_url)) {
    return {
      request_id: validation.request_id,
      success: false,
      search_verified: false,
      watch_verified: false,
      playing_verified: false,
      fullscreen_verified: false,
      title: "",
      url: searchUrl,
      stage: "search",
      attempts: stage1.attempts,
      reason:
        stage1.reason ||
        `No suitable YouTube video result found for '${query}'`
    };
  }

  const selectedVideoUrl = stage1.video_url;
  const selectedTitle = sanitizeUntrustedText(stage1.title || "");

  // Stage 2: Open watch page and verify watch URL + <video> element
  const stage2 = await runStageWithRetry(
    "open_watch",
    async (attempt) => {
      return await activeDriver.runWatchStage({
        tabId: stage1.tabId,
        videoUrl: selectedVideoUrl,
        expectedTitle: selectedTitle,
        attempt
      });
    },
    maxRetries,
    stageTimeoutMs
  );

  if (!stage2.ok || !stage2.watch_verified) {
    return {
      request_id: validation.request_id,
      success: false,
      search_verified: true,
      watch_verified: false,
      playing_verified: false,
      fullscreen_verified: false,
      title: selectedTitle,
      url: selectedVideoUrl,
      stage: "open_watch",
      attempts: stage2.attempts,
      reason:
        stage2.reason ||
        "YouTube watch page or video element could not be verified"
    };
  }

  const verifiedTitle = sanitizeUntrustedText(stage2.title || selectedTitle);
  const verifiedWatchUrl = isAllowedYouTubeWatchUrl(stage2.url)
    ? stage2.url
    : selectedVideoUrl;

  // Stage 3: Attempt playback and fullscreen within browser policy
  const stage3 = await runStageWithRetry(
    "fullscreen_and_playback",
    async (attempt) => {
      return await activeDriver.runPlaybackFullscreenStage({
        tabId: stage2.tabId || stage1.tabId,
        videoUrl: verifiedWatchUrl,
        attempt
      });
    },
    maxRetries,
    stageTimeoutMs
  );

  const playingVerified = Boolean(stage3.playing_verified);
  const fullscreenVerified = Boolean(stage3.fullscreen_verified);
  const finalTitle = sanitizeUntrustedText(stage3.title || verifiedTitle);
  const finalUrl = isAllowedYouTubeWatchUrl(stage3.url)
    ? stage3.url
    : verifiedWatchUrl;

  let reason = stage3.reason || "";
  if (!fullscreenVerified && !reason) {
    reason =
      "Chrome required a user gesture for fullscreen; click the fullscreen button or press 'f' once in the YouTube tab";
  } else if (!playingVerified && !reason) {
    reason =
      "Chrome required a user gesture to start playback";
  }

  return {
    request_id: validation.request_id,
    success: true,
    search_verified: true,
    watch_verified: true,
    playing_verified: playingVerified,
    fullscreen_verified: fullscreenVerified,
    title: finalTitle,
    url: finalUrl,
    stage: "fullscreen_and_playback",
    reason
  };
}

// ---------------------------------------------------------------------------
// close_named_tabs: close ONLY tabs on a fixed allowlist of sites
// ---------------------------------------------------------------------------

/**
 * True only for an https tab URL whose hostname exactly matches an approved host
 * for `siteKey`.  Re-checked per tab even though the query already filtered by
 * pattern, so a mismatch in query semantics can never widen what gets closed.
 */
function isApprovedTabUrl(siteKey, rawUrl) {
  const site = CLOSE_TARGET_SITES[siteKey];
  if (!site || typeof rawUrl !== "string" || !rawUrl) {
    return false;
  }
  try {
    const u = new URL(rawUrl);
    return u.protocol === "https:" && site.hosts.includes(u.hostname);
  } catch (_err) {
    return false;
  }
}

function closeFailure(requestId, target, reason, extra = {}) {
  return {
    request_id: requestId || null,
    success: false,
    verified: false,
    target: CLOSE_TARGETS.has(target) ? target : "",
    matched_count: 0,
    closed_count: 0,
    remaining_count: 0,
    per_target: {},
    reason,
    ...extra
  };
}

/**
 * Default Chrome driver for tab closing.  Only exposes: list tabs by the
 * hard-coded patterns, and remove specific numeric tab ids.
 */
function createChromeCloseDriver() {
  return {
    async queryTabs(patterns) {
      const tabs = await chrome.tabs.query({ url: patterns });
      return (tabs || []).map((t) => ({ id: t.id, url: t.url || "" }));
    },
    async removeTabs(ids) {
      await chrome.tabs.remove(ids);
    }
  };
}

/**
 * Close all tabs on the approved sites named by `target`, then RE-QUERY to verify
 * none remain.  `verified` is true only when that re-check found zero leftovers.
 * Accepts an optional `driver` ({queryTabs, removeTabs}) for deterministic tests.
 */
async function executeCloseNamedTabs(rawRequest, driver = null) {
  const validation = validateBridgeRequest(rawRequest);
  const requestId =
    rawRequest && typeof rawRequest === "object" && typeof rawRequest.request_id === "string"
      ? rawRequest.request_id
      : null;

  if (!validation.valid) {
    return closeFailure(requestId, "", validation.reason, { rejected: true });
  }
  if (validation.command !== CLOSE_TABS_COMMAND) {
    return closeFailure(validation.request_id, "", "Not a close_named_tabs request", { rejected: true });
  }

  const activeDriver = driver || createChromeCloseDriver();
  const target = validation.target;
  const siteKeys = target === "both" ? Object.keys(CLOSE_TARGET_SITES) : [target];

  const perTarget = {};
  let matched = 0;
  let closed = 0;
  let remaining = 0;

  try {
    for (const siteKey of siteKeys) {
      const site = CLOSE_TARGET_SITES[siteKey];
      const listed = await withTimeout(
        activeDriver.queryTabs(site.patterns),
        CLOSE_TIMEOUT_MS,
        `Listing ${siteKey} tabs timed out`
      );
      const ids = [];
      for (const tab of listed || []) {
        if (Number.isInteger(tab.id) && isApprovedTabUrl(siteKey, tab.url) && !ids.includes(tab.id)) {
          ids.push(tab.id);
        }
      }
      if (ids.length > MAX_CLOSE_TABS_PER_SITE) {
        return closeFailure(
          validation.request_id,
          target,
          `Refusing to close ${ids.length} ${siteKey} tabs at once (limit ${MAX_CLOSE_TABS_PER_SITE})`,
          { per_target: perTarget }
        );
      }

      if (ids.length > 0) {
        await withTimeout(
          activeDriver.removeTabs(ids),
          CLOSE_TIMEOUT_MS,
          `Closing ${siteKey} tabs timed out`
        );
      }

      // Verify: re-query and count any of the SAME tab ids still present.
      const after = await withTimeout(
        activeDriver.queryTabs(site.patterns),
        CLOSE_TIMEOUT_MS,
        `Re-checking ${siteKey} tabs timed out`
      );
      const stillOpen = (after || []).filter(
        (tab) => ids.includes(tab.id) && isApprovedTabUrl(siteKey, tab.url)
      ).length;

      perTarget[siteKey] = { matched: ids.length, closed: ids.length - stillOpen, remaining: stillOpen };
      matched += ids.length;
      closed += ids.length - stillOpen;
      remaining += stillOpen;
    }
  } catch (err) {
    return closeFailure(
      validation.request_id,
      target,
      err && err.message ? err.message : "Closing tabs failed",
      {
        matched_count: matched,
        closed_count: closed,
        remaining_count: remaining,
        per_target: perTarget
      }
    );
  }

  const ok = remaining === 0;
  return {
    request_id: validation.request_id,
    success: ok,
    verified: ok,
    target,
    matched_count: matched,
    closed_count: closed,
    remaining_count: remaining,
    per_target: perTarget,
    reason: ok
      ? ""
      : `${remaining} tab(s) are still open; Chrome may be waiting on a confirmation prompt in that tab`
  };
}

/** Route a validated native request to the one workflow that owns its command. */
async function handleNativeRequest(msg) {
  const command = msg && typeof msg === "object" && typeof msg.command === "string"
    ? msg.command.trim()
    : "";
  if (command === CLOSE_TABS_COMMAND) {
    return executeCloseNamedTabs(msg);
  }
  return executeYouTubePlayFullscreen(msg);
}

/**
 * Default Chrome Tabs + Content Script driver used in the real extension runtime.
 */
function createChromeTabDriver() {
  async function waitForTabComplete(tabId, timeoutMs = 6000) {
    const tab = await chrome.tabs.get(tabId);
    if (tab && tab.status === "complete") {
      return tab;
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(onUpdated);
        reject(new Error("Timed out waiting for YouTube tab to finish loading"));
      }, timeoutMs);

      function onUpdated(updatedTabId, changeInfo, updatedTab) {
        if (updatedTabId === tabId && changeInfo.status === "complete") {
          clearTimeout(timer);
          chrome.tabs.onUpdated.removeListener(onUpdated);
          resolve(updatedTab);
        }
      }

      chrome.tabs.onUpdated.addListener(onUpdated);
    });
  }

  async function sendStageToContent(tabId, payload) {
    return new Promise((resolve, reject) => {
      chrome.tabs.sendMessage(tabId, payload, (response) => {
        const lastErr = chrome.runtime.lastError;
        if (lastErr) {
          reject(new Error(lastErr.message || "Content script communication error"));
          return;
        }
        resolve(response || { ok: false, reason: "Empty response from content script" });
      });
    });
  }

  async function findOrOpenYouTubeTab(targetUrl) {
    const existingTabs = await chrome.tabs.query({
      url: ["https://www.youtube.com/*", "https://youtu.be/*"]
    });
    if (existingTabs && existingTabs.length > 0) {
      const activeTab = existingTabs.find((t) => t.active) || existingTabs[0];
      const updated = await chrome.tabs.update(activeTab.id, {
        url: targetUrl,
        active: true
      });
      await waitForTabComplete(updated.id, 12000);
      return updated.id;
    }
    const created = await chrome.tabs.create({
      url: targetUrl,
      active: true
    });
    await waitForTabComplete(created.id, 12000);
    return created.id;
  }

  return {
    async runSearchStage({ query, searchUrl }) {
      const tabId = await findOrOpenYouTubeTab(searchUrl);
      const res = await sendStageToContent(tabId, {
        type: "lexi_stage",
        stage: "search",
        query
      });
      return { ...res, tabId };
    },

    async runWatchStage({ tabId, videoUrl }) {
      await chrome.tabs.update(tabId, { url: videoUrl, active: true });
      await waitForTabComplete(tabId, 12000);
      const res = await sendStageToContent(tabId, {
        type: "lexi_stage",
        stage: "verify_watch",
        expected_url: videoUrl
      });
      return { ...res, tabId };
    },

    async runPlaybackFullscreenStage({ tabId }) {
      const res = await sendStageToContent(tabId, {
        type: "lexi_stage",
        stage: "playback_and_fullscreen"
      });
      return { ...res, tabId };
    }
  };
}

// ---------------------------------------------------------------------------
// Native Messaging Connection Lifecycle (active only in Chrome extension env)
// ---------------------------------------------------------------------------
let nativePort = null;
let reconnectTimer = null;

function ensureNativeConnection() {
  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.connectNative) {
    return null;
  }
  if (nativePort) {
    return nativePort;
  }

  try {
    nativePort = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    nativePort.onMessage.addListener((msg) => {
      if (msg && msg.type === "lexi_host_ping") {
        try {
          nativePort.postMessage({ type: "lexi_host_pong", timestamp: Date.now() });
        } catch (_e) {}
        return;
      }

      handleNativeRequest(msg)
        .then((result) => {
          if (nativePort) {
            nativePort.postMessage(result);
          }
        })
        .catch((err) => {
          if (nativePort) {
            const reqId = msg && msg.request_id ? msg.request_id : null;
            const reason = err && err.message ? err.message : "Extension workflow error";
            nativePort.postMessage(
              msg && msg.command === CLOSE_TABS_COMMAND
                ? closeFailure(reqId, "", reason)
                : {
                    request_id: reqId,
                    success: false,
                    search_verified: false,
                    watch_verified: false,
                    playing_verified: false,
                    fullscreen_verified: false,
                    title: "",
                    url: "",
                    reason
                  }
            );
          }
        });
    });

    nativePort.onDisconnect.addListener(() => {
      nativePort = null;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        ensureNativeConnection();
      }, 2000);
    });
  } catch (_err) {
    nativePort = null;
  }
  return nativePort;
}

if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onStartup) {
  chrome.runtime.onStartup.addListener(() => {
    ensureNativeConnection();
  });

  chrome.runtime.onInstalled.addListener(() => {
    ensureNativeConnection();
    if (chrome.alarms) {
      chrome.alarms.create("lexi_native_keepalive", { periodInMinutes: 0.5 });
    }
  });

  if (chrome.alarms && chrome.alarms.onAlarm) {
    chrome.alarms.onAlarm.addListener((alarm) => {
      if (alarm && alarm.name === "lexi_native_keepalive") {
        ensureNativeConnection();
      }
    });
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "lexi_content_ready") {
      ensureNativeConnection();
      sendResponse({ ok: true });
      return false;
    }
    return false;
  });

  ensureNativeConnection();
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    NATIVE_HOST_NAME,
    ALLOWED_COMMANDS,
    STAGE_TIMEOUT_MS,
    MAX_STAGE_RETRIES,
    sanitizeUntrustedText,
    isAllowedYouTubeWatchUrl,
    validateBridgeRequest,
    executeYouTubePlayFullscreen,
    CLOSE_TABS_COMMAND,
    CLOSE_TARGET_SITES,
    isApprovedTabUrl,
    executeCloseNamedTabs,
    handleNativeRequest
  };
}
