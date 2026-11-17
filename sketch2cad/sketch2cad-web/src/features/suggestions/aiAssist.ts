// AI assistance settings of the editor (jotdown H5): the `ai_assist.enabled` switch
// (`GET /config/features`) and the confidence below which a suggestion is highlighted
// (`suggestions.min_confidence_highlight` of `GET /config/params`). Each is requested once per API
// client and shared by every component that asks; a failed request is not kept, so the next mount
// asks again.
//
// A failed or pending switch request counts as "off": the suggestion controls only appear once
// the server says the feature is on (H5-7). The threshold falls back to 0.7 until it is read, when
// the request fails, or when the value is not a number in [0, 1]. Overrides per incident type are
// not applied: the editor does not know the case's incident type, so the global override (or the
// default) is used.
import { useEffect, useState } from "react";
import type { ApiClient } from "../../api/client";
import type { components } from "../../api/schema";

type ConfigApi = Pick<ApiClient, "get">;
type ParamOut = components["schemas"]["ParamOut"];

export const HIGHLIGHT_PARAM = "suggestions.min_confidence_highlight";
export const DEFAULT_HIGHLIGHT_THRESHOLD = 0.7;

const featureRequests = new WeakMap<ConfigApi, Promise<boolean>>();
const thresholdRequests = new WeakMap<ConfigApi, Promise<number>>();

function once<T>(cache: WeakMap<ConfigApi, Promise<T>>, api: ConfigApi, load: () => Promise<T>) {
  let request = cache.get(api);
  if (request === undefined) {
    request = load();
    cache.set(api, request);
    request.catch(() => cache.delete(api));
  }
  return request;
}

/** Value of `param` without incident type: its global override, else its default. */
export function globalParamValue(param: ParamOut): ParamOut["default"] {
  return param.overrides.find((override) => override.scope === "global")?.value ?? param.default;
}

export function aiAssistEnabled(api: ConfigApi): Promise<boolean> {
  return once(featureRequests, api, () =>
    api.get("/api/v1/config/features").then((features) => features.ai_assist.enabled),
  );
}

export function highlightThreshold(api: ConfigApi): Promise<number> {
  return once(thresholdRequests, api, () =>
    api.get("/api/v1/config/params").then(({ params }) => {
      const param = params.find((p) => p.name === HIGHLIGHT_PARAM);
      const value = param === undefined ? undefined : globalParamValue(param);
      return typeof value === "number" && value >= 0 && value <= 1
        ? value
        : DEFAULT_HIGHLIGHT_THRESHOLD;
    }),
  );
}

/** `true` once the server reports AI assistance on; `false` while unknown, off or unreadable. */
export function useAiAssistEnabled(api: ConfigApi): boolean {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    let active = true;
    aiAssistEnabled(api).then(
      (value) => active && setEnabled(value),
      () => active && setEnabled(false),
    );
    return () => {
      active = false;
    };
  }, [api]);
  return enabled;
}

/** Highlight threshold; DEFAULT_HIGHLIGHT_THRESHOLD until read. `skip` avoids the request. */
export function useHighlightThreshold(api: ConfigApi, skip = false): number {
  const [threshold, setThreshold] = useState(DEFAULT_HIGHLIGHT_THRESHOLD);
  useEffect(() => {
    if (skip) return;
    let active = true;
    highlightThreshold(api).then(
      (value) => active && setThreshold(value),
      () => active && setThreshold(DEFAULT_HIGHLIGHT_THRESHOLD),
    );
    return () => {
      active = false;
    };
  }, [api, skip]);
  return threshold;
}

/** Whether a suggestion's confidence calls for the highlight; an unknown confidence does. */
export function isLowConfidence(confidence: number | null | undefined, threshold: number): boolean {
  return confidence == null || confidence < threshold;
}
