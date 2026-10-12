"""Tests for stlite / Streamlit version compatibility.

Option 1 approach: the stlite bundle ships Streamlit >=1.50 (DEFAULT_STLITE_VERSION
>= 0.90.0), so width="stretch" is a valid API in views/.

NOTE ON WHAT THIS FILE CAN AND CANNOT PROVE: every check below is a STATIC check
of a constant (DEFAULT_STLITE_VERSION) and/or a source-text scan. None of it
executes stlite/Pyodide, so it CANNOT detect whether the deployed bundle actually
provides the API the views call at runtime. That gap is not hypothetical: this
exact static check was green (MIN_STLITE_VERSION asserted only >= 0.80.0) while
the live deployed site crashed, because 0.80.0-0.89.x do NOT bundle Streamlit
>=1.50 despite an earlier comment here claiming otherwise. A runtime test is not
practical because the defect only manifests inside a live Streamlit + stlite
rendering loop (Pyodide-only), not in a headless pytest session — so treat these
assertions as a floor, not a guarantee.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWS_DIR = REPO_ROOT / "views"
# First stlite release bundling Streamlit >=1.50 (CHANGELOG: "[0.90.0] -
# 2025-11-13: Update Streamlit to 1.50.0, #1611"). stlite 0.80.0 only bundles
# Streamlit 1.41.0 -- the prior comment here claiming 0.80.0 was the minimum
# was factually wrong and let a real runtime crash pass this test.
MIN_STLITE_VERSION = (0, 90, 0)

# Exclusive UPPER bound. stlite 1.9.0+ bundles Streamlit 1.62.0, whose
# data_editor raises `NameError: name 'arrow_table' is not defined` for any
# st.data_editor(key=..., num_rows="fixed") call -- the default num_rows. Both
# grids in views/ use that exact shape, so 1.9.x takes the Conversion Planner
# and the Option Exercise grid down on the public site while local tests, which
# run Streamlit 1.58.0, stay green. See deploy/build_stlite.py for the full
# stlite -> Streamlit bundle map and the live verification.
MAX_STLITE_VERSION_EXCLUSIVE = (1, 9, 0)


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _collect_view_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(VIEWS_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        out[rel] = p.read_text(encoding="utf-8")
    return out


def _load_build_stlite_mod():
    spec = importlib.util.spec_from_file_location(
        "build_stlite", REPO_ROOT / "deploy" / "build_stlite.py"
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestStliteVersionMinimum:
    """DEFAULT_STLITE_VERSION in deploy/build_stlite.py must be >=0.90.0.

    stlite 0.90.0 is the first release that bundles Streamlit >=1.50, which
    introduced the width="stretch" argument for st.dataframe / st.plotly_chart
    / st.button. (stlite 0.80.0 only bundles Streamlit 1.41.0 and does NOT
    have this API -- do not confuse the stlite version number with the
    Streamlit version it bundles.)
    """

    def test_default_stlite_version_meets_minimum(self) -> None:
        """DEFAULT_STLITE_VERSION must be >= 0.90.0 (bundles Streamlit >=1.50)."""
        mod = _load_build_stlite_mod()
        version_str = str(mod.DEFAULT_STLITE_VERSION)
        version = _parse_version(version_str)
        min_str = ".".join(str(v) for v in MIN_STLITE_VERSION)
        assert version >= MIN_STLITE_VERSION, (
            f"DEFAULT_STLITE_VERSION={version_str!r} is below minimum "
            f"{min_str} required to bundle Streamlit >=1.50 "
            '(needed for width="stretch" support)'
        )

    def test_default_stlite_version_below_broken_ceiling(self) -> None:
        """DEFAULT_STLITE_VERSION must be < 1.9.0 (1.9.x breaks st.data_editor)."""
        mod = _load_build_stlite_mod()
        parsed = _parse_version(str(mod.DEFAULT_STLITE_VERSION))
        assert parsed < MAX_STLITE_VERSION_EXCLUSIVE, (
            f"DEFAULT_STLITE_VERSION={mod.DEFAULT_STLITE_VERSION} is >= "
            f"{'.'.join(str(x) for x in MAX_STLITE_VERSION_EXCLUSIVE)}, which bundles "
            "Streamlit 1.62.0 and raises NameError: name 'arrow_table' is not defined "
            "on every st.data_editor(key=..., num_rows='fixed') call -- both grids in "
            "views/ use that shape, so the public site's Conversion Planner and Option "
            "Exercise grid would be dead on arrival"
        )


class TestWidthStretchUsedInViews:
    """At least one view must use width="stretch", confirming the Streamlit >=1.50 API.

    This is a positive-direction guard: if views are reverted to use_container_width=True
    this test catches it, prompting a corresponding stlite version bump.
    """

    def test_width_stretch_present_in_views(self) -> None:
        """At least one view must use width="stretch" (the Streamlit >=1.50 API)."""
        sources = _collect_view_sources()
        found = any('width="stretch"' in source for source in sources.values())
        assert found, (
            'width="stretch" not found in any view — '
            "expected Streamlit >=1.50 API to be in use. "
            "If views were reverted to use_container_width=True, also update "
            "DEFAULT_STLITE_VERSION and this test."
        )


class TestStliteVersionCoversUsedStreamlitApis:
    """Derive the required stlite floor from the Streamlit APIs actually used in
    views/, rather than restating a hardcoded constant twice.

    If a future contributor adopts a newer Streamlit-only API without bumping
    DEFAULT_STLITE_VERSION to match, this test fails with an explanation of the
    coupling -- instead of silently passing like the old MIN_STLITE_VERSION did.
    """

    # {marker string found in views/ source: (min Streamlit version, min stlite
    # version that first bundles it)}. Extend this mapping when views/ starts
    # relying on a newer Streamlit-only API.
    API_STLITE_FLOORS: dict[str, tuple[str, tuple[int, int, int]]] = {
        'width="stretch"': ("1.49", (0, 90, 0)),
    }

    def test_stlite_version_covers_apis_used_in_views(self) -> None:
        sources = _collect_view_sources()
        combined = "\n".join(sources.values())
        mod = _load_build_stlite_mod()
        actual_version = _parse_version(str(mod.DEFAULT_STLITE_VERSION))

        for marker, (streamlit_min, stlite_min) in self.API_STLITE_FLOORS.items():
            if marker not in combined:
                continue
            stlite_min_str = ".".join(str(v) for v in stlite_min)
            assert actual_version >= stlite_min, (
                f"views/ use {marker!r} (requires Streamlit >={streamlit_min}), "
                f"which needs stlite >={stlite_min_str}, but "
                f"DEFAULT_STLITE_VERSION={mod.DEFAULT_STLITE_VERSION!r} in "
                "deploy/build_stlite.py is below that floor. Bump "
                "DEFAULT_STLITE_VERSION (and its API_STLITE_FLOORS entry here "
                "if you're adding a new API) so the deployed bundle actually "
                "provides the API the views call, or the public site will "
                "crash at runtime even though this file's other checks pass."
            )


class TestTemplateReferencesCurrentStlitePackage:
    """deploy/template.html must reference @stlite/browser, not the retired @stlite/mountable name.

    @stlite/mountable was renamed to @stlite/browser at v0.76.0 and never published a
    newer version under the old name — referencing @stlite/mountable at any version
    >=0.76.0 means the CDN URL 404s and the public site never boots. Caught in production
    when DEFAULT_STLITE_VERSION was bumped to 0.80.0 (PR #202) without updating the
    package name in template.html.
    """

    def test_template_does_not_reference_retired_mountable_package(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "@stlite/mountable" not in template, (
            "deploy/template.html references the retired @stlite/mountable package "
            "(renamed to @stlite/browser at v0.76.0, never published past 0.75.0) — "
            "this 404s at DEFAULT_STLITE_VERSION >= 0.76.0"
        )
        assert "@stlite/browser" in template, (
            "deploy/template.html should reference @stlite/browser (the current "
            "package name post-rename)"
        )


class TestTemplateSurfacesPyodideWasmFatal:
    """deploy/template.html must classify and surface loud Pyodide/WASM fatals
    that occur on the MAIN THREAD.

    NOTE ON SCOPE -- this does NOT guard the observed production wedge:
    `__isPyodideWasmFatal`/`__handlePyodideWasmFatal` are wired only into
    `window`'s `error` and `unhandledrejection` listeners, so they can only
    ever catch a fatal that reaches the main thread's global scope. The real
    production wedge on stlite 1.9.1 ("RuntimeError: memory access out of
    bounds") originates inside the Pyodide WEB WORKER, whose global scope is
    never reachable from `window` per platform semantics -- verified live in
    a browser: the fatal appeared in DevTools while these window listeners
    never fired and no `[stlite][wasm-fatal]` log was ever emitted. That
    worker-origin case is instead covered by the main-thread liveness
    heartbeat (see TestTemplateSurfacesLivenessHeartbeat below), which polls
    for liveness rather than waiting for a signal that can never arrive from
    the worker. These listeners remain a valid regression guard for a
    genuinely main-thread Pyodide/WASM fatal, should one ever occur. This is
    a static source-text scan only -- see the module docstring's caveat about
    what these checks can and cannot prove.
    """

    def test_wasm_fatal_patterns_present(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        for pattern in [
            "memory access out of bounds",
            "table index is out of bounds",
            "Recursive call to fatal_error",
            "unreachable executed",
        ]:
            assert pattern in template, (
                f"deploy/template.html no longer matches WASM fatal pattern {pattern!r} -- "
                "the loud Pyodide/WASM fatal would go undetected again"
            )

    def test_wasm_fatal_classifier_is_latched(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "wasmFatalShown" in template, (
            "deploy/template.html must latch the WASM fatal banner (it fires in a "
            "storm of dozens of repeats per freeze) -- latch variable not found"
        )
        assert "__isPyodideWasmFatal" in template, (
            "deploy/template.html must wire a WASM-fatal classifier into both the "
            "error and unhandledrejection listeners"
        )
        assert "__handlePyodideWasmFatal" in template, (
            "deploy/template.html must wire a WASM-fatal classifier into both the "
            "error and unhandledrejection listeners"
        )


class TestTemplateSurfacesLivenessHeartbeat:
    """deploy/template.html must actively poll the stlite kernel for liveness.

    stlite's Pyodide worker installs ZERO global fault handlers and its
    postMessage protocol has no "worker died" message type -- event:loadError
    is load-time only, and reply.error is scoped to a single in-flight
    command. A fatal on an un-awaited await inside the worker's async
    dispatcher becomes an unhandled rejection on the WORKER's own global
    scope, which per platform semantics never reaches this page's window, so
    none of window/error/unhandledrejection listeners (including PR #507's
    dead-channel detector and PR #508's respawn detector) can ever fire for
    it. The worker script is an inlined data: URI baked into stlite's CDN
    bundle and cannot be patched. The only way to detect this failure class
    is a main-thread heartbeat: a trivial runPython() round trip (the one
    controller API that requires an actual worker reply) probed on an
    interval, with a timeout treated as evidence of a wedge. This is a
    static source-text scan only -- see the module docstring's caveat about
    what these checks can and cannot prove.
    """

    def test_heartbeat_probes_via_run_python_round_trip(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "controller.runPython(" in template, (
            "deploy/template.html must probe liveness via controller.runPython(), "
            "the one mount()-returned API that requires an actual worker reply "
            "(reply:run_python) -- without this, a worker-origin fatal that emits "
            "no signal at all is undetectable"
        )
        assert "controller = mount(" in template, (
            "deploy/template.html must capture mount()'s return value so the "
            "heartbeat can call runPython() on it"
        )

    def test_heartbeat_is_armed_only_after_load_completes(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "__armHeartbeat" in template, (
            "deploy/template.html must arm the heartbeat via a dedicated "
            "post-loader hook (mirroring __armKernelRespawnDetector), never "
            "probing during startup itself"
        )
        assert "window.__armHeartbeat?.(controller)" in template, (
            "deploy/template.html must arm the heartbeat from the same "
            "post-loader callback that arms the kernel respawn detector"
        )

    def test_heartbeat_uses_conservative_forgiving_defaults(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "HEARTBEAT_INTERVAL_MS = 30000" in template, (
            "heartbeat probe interval must be 30s -- a tighter interval risks "
            "false positives against legitimately slow operations (e.g. PDF parsing)"
        )
        assert "HEARTBEAT_TIMEOUT_MS = 90000" in template, (
            "heartbeat per-probe timeout must be 90s -- a probe queued behind a "
            "long-running Python task is indistinguishable from a dead kernel, "
            "so the timeout must be forgiving"
        )
        assert "HEARTBEAT_MISS_THRESHOLD = 2" in template, (
            "heartbeat must require 2 consecutive timed-out probes before "
            "reporting anything, not a single miss"
        )

    def test_heartbeat_never_overlaps_probes(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "heartbeatInFlight" in template, (
            "deploy/template.html must skip a tick rather than stack a new probe "
            "while one is already in flight"
        )

    def test_heartbeat_banner_is_latched(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "heartbeatLatched" in template, (
            "deploy/template.html must latch the heartbeat banner (show once, "
            "then stop probing entirely) -- latch variable not found"
        )
        assert "__stliteShowBanner" in template, (
            "the heartbeat must reuse the existing banner mechanism, not build a second one"
        )

    def test_heartbeat_wording_says_not_responding_not_crashed(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "has stopped responding" in template, (
            "the heartbeat banner must say the runtime is NOT RESPONDING, not "
            "that it crashed -- from the main thread a timeout cannot "
            "distinguish 'busy' from 'dead', so the message must not overclaim"
        )

    def test_heartbeat_logs_greppable_console_error(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "[stlite][heartbeat]" in template, (
            "deploy/template.html must log a console.error with the "
            "[stlite][heartbeat] prefix, consistent with the existing "
            "[stlite][wasm-fatal] convention, on latch"
        )


class TestKernelRespawnArmsFromFirstKernelLoadNotFromHideLoader:
    """The respawn grace timer must start from the FIRST KERNEL-LOAD EVENT,
    not from hideLoader() clearing the loading panel.

    Measured live in a browser: hideLoader() fires ~4.7s after navigation and
    used to start a 15s grace timer immediately, arming the detector at
    ~19.7s. stlite's real first "The kernel has been loaded" console.debug
    log lands ~20-22s on the live app -- AFTER arming -- so the legitimate
    first kernel load was misreported as a respawn on every single visit,
    permanently latching the shared banner and hiding the accurate heartbeat
    and WASM-fatal banners behind it (see TestBannerPriorityMechanism below
    for why that latch mattered). Re-keying the same 15s grace to the first
    kernel-load event instead makes boot duration irrelevant to arming, while
    still exempting stlite 1.9.x's benign same-second double kernel-load log
    (comfortably inside the 15s grace either way).
    """

    @staticmethod
    def _console_debug_patch_region(template: str) -> str:
        start = template.index("console.debug = function")
        end = template.index("return originalDebug(...args);", start)
        return template[start:end]

    @staticmethod
    def _arm_kernel_respawn_detector_region(template: str) -> str:
        start = template.index("window.__armKernelRespawnDetector = () => {")
        end = template.index("};", start)
        return template[start:end]

    def test_respawn_arm_delay_is_started_inside_console_debug_patch(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        region = self._console_debug_patch_region(template)
        assert "RESPAWN_ARM_DELAY_MS" in region, (
            "the respawn grace timer must be started from inside the "
            "console.debug patch, on the first kernel-load event -- "
            "RESPAWN_ARM_DELAY_MS was not found in that region, so arming may "
            "still be keyed to something else (e.g. hideLoader()) and could "
            "race the real first kernel-load log again"
        )

    def test_arm_kernel_respawn_detector_no_longer_starts_a_timer(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        region = self._arm_kernel_respawn_detector_region(template)
        assert "setTimeout" not in region, (
            "window.__armKernelRespawnDetector must no longer start the grace "
            "timer itself -- it is called from hideLoader() at ~4.7s post-nav, "
            "which races stlite's real first kernel-load log at ~20-22s and "
            "arms BEFORE that legitimate first load, permanently mis-flagging "
            "it as a respawn. The grace timer must instead start from the "
            "first kernel-load console.debug event (see the console.debug "
            "patch region)."
        )


class TestBannerPriorityMechanism:
    """__stliteShowBanner must use a numeric priority, not a single boolean latch.

    All four banner classes (respawn, generic/startup, heartbeat, WASM-fatal)
    share one DOM element. A boolean "first banner wins forever" latch meant
    the benign respawn false-positive covered above permanently suppressed
    the accurate heartbeat and WASM-fatal banners rendered after it -- the
    single worst-case outcome this pairing of fixes exists to prevent. A more
    severe banner must be able to supersede a less severe one already shown,
    and a less severe one must never overwrite a more severe one already
    shown.
    """

    @staticmethod
    def _banner_call_level_name(template: str, call_start: int) -> str:
        """Extract the `level` argument identifier from a
        `window.__stliteShowBanner(msg, LEVEL)` call starting at call_start."""
        call_end = template.index(");", call_start)
        call_text = template[call_start:call_end]
        last_comma = call_text.rindex(",")
        return call_text[last_comma + 1 :].strip()

    @staticmethod
    def _declared_level_value(template: str, level_name: str) -> int:
        """Resolve a `const BANNER_LEVEL_* = N;` declaration to its integer N."""
        marker = f"const {level_name} ="
        start = template.index(marker) + len(marker)
        end = template.index(";", start)
        return int(template[start:end].strip())

    def test_boolean_banner_latch_is_gone(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "let bannerShown = false;" not in template, (
            "deploy/template.html still declares the old boolean bannerShown "
            "latch -- it must be replaced with a numeric priority level so a "
            "more severe banner can supersede a less severe one already shown"
        )
        assert "bannerLevelShown" in template, (
            "deploy/template.html must track the currently-shown banner's "
            "priority level (bannerLevelShown) so severity comparisons between "
            "banner calls are possible"
        )

    def test_wasm_fatal_call_site_outranks_respawn_call_site(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")

        wasm_fatal_fn_idx = template.index("function __handlePyodideWasmFatal(")
        wasm_fatal_call = template.index("window.__stliteShowBanner(", wasm_fatal_fn_idx)
        wasm_fatal_level_name = self._banner_call_level_name(template, wasm_fatal_call)
        wasm_fatal_level = self._declared_level_value(template, wasm_fatal_level_name)

        respawn_msg_idx = template.index('"The Python runtime restarted unexpectedly')
        respawn_call = template.rindex("window.__stliteShowBanner(", 0, respawn_msg_idx)
        respawn_level_name = self._banner_call_level_name(template, respawn_call)
        respawn_level = self._declared_level_value(template, respawn_level_name)

        assert wasm_fatal_level > respawn_level, (
            "the WASM-fatal banner call site must use a strictly higher "
            f"priority level ({wasm_fatal_level}) than the respawn banner call "
            f"site ({respawn_level}) -- an unrecoverable crash must never be "
            "masked by an earlier, less severe respawn banner"
        )
