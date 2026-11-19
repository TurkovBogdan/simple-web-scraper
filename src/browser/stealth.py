"""Stealth init-script for headless Chromium.

`build_stealth_js(profile)` builds the JS patch with a concrete fingerprint
profile. All variable values (UA, GPU, locale, hardware) are substituted through
the `_CFG` block at the top; the patch structure (toString masking, plugins,
canvas/audio noise, window.chrome) is identical across profiles.

Ported verbatim from simple-scraper-api — see memory ref [[ref-simple-scraper-api]].
Any change to profile values must be checked against a reference Chrome via
fingerprint.com / creepjs, or it breaks what already works.
"""
from __future__ import annotations

import json

from browser.profile import BrowserProfile


def build_stealth_js(profile: BrowserProfile) -> str:
    cfg = {
        "userAgent": profile.user_agent,
        "appVersion": profile.app_version,
        "platform": profile.navigator.platform,
        "vendor": profile.navigator.vendor,
        "language": profile.primary_language,
        "languages": profile.languages,
        "hardwareConcurrency": profile.navigator.hardware_concurrency,
        "deviceMemory": profile.navigator.device_memory,
        "maxTouchPoints": profile.navigator.max_touch_points,
        "uad": {
            "brands": [b.model_dump() for b in profile.user_agent_data.brands],
            "fullVersionList": [b.model_dump() for b in profile.user_agent_data.full_version_list],
            "platform": profile.user_agent_data.platform,
            "platformVersion": profile.user_agent_data.platform_version,
            "architecture": profile.user_agent_data.architecture,
            "bitness": profile.user_agent_data.bitness,
            "mobile": profile.user_agent_data.mobile,
            "model": profile.user_agent_data.model,
            "uaFullVersion": profile.user_agent_data.ua_full_version,
        },
        "webgl": {
            "vendor": profile.webgl.vendor,
            "renderer": profile.webgl.renderer,
        },
        "screen": {
            "width": profile.screen.width,
            "height": profile.screen.height,
            "availWidth": profile.screen.avail_width,
            "availHeight": profile.screen.avail_height,
            "colorDepth": profile.screen.color_depth,
        },
        "devicePixelRatio": profile.device_pixel_ratio,
    }
    cfg_json = json.dumps(cfg, ensure_ascii=False)
    return _STEALTH_TEMPLATE.replace("/*__CFG__*/", cfg_json)


_STEALTH_TEMPLATE = """
(function () {
    const _CFG = /*__CFG__*/;

    // ── 1. Mask custom functions to look native ──────────────────────────────
    const _origFnToString = Function.prototype.toString;
    const _nativeMap = new WeakMap();

    Object.defineProperty(Function.prototype, 'toString', {
        value: function toString() {
            if (_nativeMap.has(this)) return _nativeMap.get(this);
            return _origFnToString.call(this);
        },
        configurable: true, writable: true, enumerable: false,
    });
    _nativeMap.set(Function.prototype.toString,
        'function toString() { [native code] }');

    const asNative = (fn, name) => {
        _nativeMap.set(fn, `function ${name ?? fn.name ?? ''}() { [native code] }`);
        return fn;
    };

    // ── 2. Properties on Navigator.prototype, not the instance ───────────────
    const _navOverrides = new Set();
    const defNav = (prop, val) => {
        const getter = asNative(() => val, `get ${prop}`);
        Object.defineProperty(Navigator.prototype, prop, {
            get: getter, configurable: true, enumerable: true,
        });
        _navOverrides.add(prop);
    };

    defNav('webdriver',           false);
    defNav('platform',            _CFG.platform);
    defNav('vendor',              _CFG.vendor);
    defNav('vendorSub',           '');
    defNav('language',            _CFG.language);
    defNav('languages',           Object.freeze(_CFG.languages.slice()));
    defNav('hardwareConcurrency', _CFG.hardwareConcurrency);
    defNav('deviceMemory',        _CFG.deviceMemory);
    defNav('maxTouchPoints',      _CFG.maxTouchPoints);
    defNav('userAgent',           _CFG.userAgent);
    defNav('appVersion',          _CFG.appVersion);

    // ── 3. userAgentData ─────────────────────────────────────────────────────
    const _uad = {
        brands: _CFG.uad.brands, mobile: _CFG.uad.mobile, platform: _CFG.uad.platform,
        getHighEntropyValues: asNative(async () => ({
            architecture: _CFG.uad.architecture,
            bitness: _CFG.uad.bitness,
            brands: _CFG.uad.brands,
            fullVersionList: _CFG.uad.fullVersionList,
            mobile: _CFG.uad.mobile,
            model: _CFG.uad.model,
            platform: _CFG.uad.platform,
            platformVersion: _CFG.uad.platformVersion,
            uaFullVersion: _CFG.uad.uaFullVersion,
        }), 'getHighEntropyValues'),
        toJSON: asNative(
            () => ({ brands: _CFG.uad.brands, mobile: _CFG.uad.mobile, platform: _CFG.uad.platform }),
            'toJSON'
        ),
    };
    defNav('userAgentData', _uad);

    // ── 4. plugins / mimeTypes ───────────────────────────────────────────────
    const _makeMime = (type) => ({
        type, suffixes: 'pdf', description: 'Portable Document Format',
    });
    const _mimes = [_makeMime('application/pdf'), _makeMime('text/pdf')];

    const _makePlugin = (name) => {
        const p = Object.create(null);
        Object.defineProperties(p, {
            name:        { value: name,                    enumerable: true },
            filename:    { value: 'internal-pdf-viewer',   enumerable: true },
            description: { value: 'Portable Document Format', enumerable: true },
            length:      { value: 2,                       enumerable: true },
            0:           { value: _mimes[0],               enumerable: true },
            1:           { value: _mimes[1],               enumerable: true },
        });
        p.item       = asNative((i) => _mimes[i] ?? null, 'item');
        p.namedItem  = asNative((t) => _mimes.find(m => m.type === t) ?? null, 'namedItem');
        p[Symbol.iterator] = function* () { yield* _mimes; };
        return p;
    };

    const _plugNames = [
        'PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer',
        'Microsoft Edge PDF Viewer', 'WebKit built-in PDF',
    ];
    const _plugArr = _plugNames.map(_makePlugin);

    const _makeArrayLike = (items, namedKey) => {
        const o = Object.create(null);
        items.forEach((x, i) => { o[i] = x; });
        Object.defineProperty(o, 'length', { value: items.length, enumerable: true });
        o.item      = asNative((i) => items[i] ?? null, 'item');
        o.namedItem = asNative((n) => items.find(x => x[namedKey] === n) ?? null, 'namedItem');
        o.refresh   = asNative(() => {}, 'refresh');
        o[Symbol.iterator] = function* () { yield* items; };
        return o;
    };

    defNav('plugins',   _makeArrayLike(_plugArr,  'name'));
    defNav('mimeTypes', _makeArrayLike(_mimes,    'type'));

    // ── 4b. Screen.prototype + devicePixelRatio ──────────────────────────────
    const _scrOverrides = new Set();
    const defScreen = (prop, val) => {
        const getter = asNative(() => val, `get ${prop}`);
        Object.defineProperty(Screen.prototype, prop, {
            get: getter, configurable: true, enumerable: true,
        });
        _scrOverrides.add(prop);
    };
    defScreen('width',       _CFG.screen.width);
    defScreen('height',      _CFG.screen.height);
    defScreen('availWidth',  _CFG.screen.availWidth);
    defScreen('availHeight', _CFG.screen.availHeight);
    defScreen('availLeft',   0);
    defScreen('availTop',    0);
    defScreen('colorDepth',  _CFG.screen.colorDepth);
    defScreen('pixelDepth',  _CFG.screen.colorDepth);

    Object.defineProperty(window, 'devicePixelRatio', {
        get: asNative(() => _CFG.devicePixelRatio, 'get devicePixelRatio'),
        configurable: true, enumerable: true,
    });

    // ── 5. Object.getOwnPropertyDescriptor — hide the prototype patches ──────
    const _origOGOPD = Object.getOwnPropertyDescriptor.bind(Object);
    Object.getOwnPropertyDescriptor = asNative(function getOwnPropertyDescriptor(obj, prop) {
        if (obj === Navigator.prototype && _navOverrides.has(prop)) {
            return {
                get: asNative(() => {}, `get ${prop}`),
                set: undefined, enumerable: true, configurable: true,
            };
        }
        if (obj === Screen.prototype && _scrOverrides.has(prop)) {
            return {
                get: asNative(() => {}, `get ${prop}`),
                set: undefined, enumerable: true, configurable: true,
            };
        }
        return _origOGOPD(obj, prop);
    }, 'getOwnPropertyDescriptor');

    // ── 6. window.chrome ─────────────────────────────────────────────────────
    window.chrome = {
        app: {
            isInstalled: false,
            InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
            RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
        },
        loadTimes: asNative(function loadTimes() {
            const t = performance.timing;
            return {
                requestTime: t.navigationStart / 1000, startLoadTime: t.navigationStart / 1000,
                commitLoadTime: t.responseStart / 1000,
                finishDocumentLoadTime: t.domContentLoadedEventEnd / 1000,
                finishLoadTime: t.loadEventEnd / 1000,
                firstPaintTime: 0, firstPaintAfterLoadTime: 0,
                navigationType: 'Other', wasFetchedViaSpdy: true,
                wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2',
                wasAlternateProtocolAvailable: false, connectionInfo: 'h2',
            };
        }, 'loadTimes'),
        csi: asNative(function csi() {
            return { startE: Date.now(), onloadT: Date.now(), pageT: performance.now(), tran: 15 };
        }, 'csi'),
    };

    // ── 7. WebGL ─────────────────────────────────────────────────────────────
    const _origGetCtx  = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = asNative(function getContext(type, ...args) {
        const ctx = _origGetCtx.call(this, type, ...args);
        if (ctx && (type === 'webgl' || type === 'webgl2')) {
            const orig = ctx.getParameter.bind(ctx);
            ctx.getParameter = asNative(function getParameter(p) {
                if (p === 37445) return _CFG.webgl.vendor;
                if (p === 37446) return _CFG.webgl.renderer;
                return orig(p);
            }, 'getParameter');
        }
        return ctx;
    }, 'getContext');

    // ── 8. Canvas noise ───────────────────────────────────────────────────────
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = asNative(function toDataURL(type, ...args) {
        const ctx2d = _origGetCtx.call(this, '2d');
        if (ctx2d) {
            const img = ctx2d.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < img.data.length; i += 4) {
                if (Math.random() < 0.01) img.data[i]     = Math.min(255, img.data[i]     + 1);
                if (Math.random() < 0.01) img.data[i + 1] = Math.min(255, img.data[i + 1] + 1);
            }
            ctx2d.putImageData(img, 0, 0);
        }
        return _origToDataURL.call(this, type, ...args);
    }, 'toDataURL');

    // ── 9. AudioContext noise ─────────────────────────────────────────────────
    const _AC = window.AudioContext || window.webkitAudioContext;
    if (_AC) {
        const _origCreateDC = _AC.prototype.createDynamicsCompressor;
        _AC.prototype.createDynamicsCompressor = asNative(function createDynamicsCompressor(...a) {
            const node = _origCreateDC.apply(this, a);
            node.threshold.value += (Math.random() - 0.5) * 0.0001;
            return node;
        }, 'createDynamicsCompressor');
    }
})();
"""


__all__ = ["build_stealth_js"]
