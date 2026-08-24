<!--
	Hand-rolled 3D orbit widget for CameraShotField. A stylized bust is projected
	from a handful of 3D points (no three.js / no deps) and rotated by a draggable
	camera orbit; the parent quantizes the pose to canonical shots. Colors come from
	Instrument tokens via scoped classes (rgb(var(--token))), so it re-themes with
	no JS. Keyboard: arrows nudge azimuth/elevation; the grid view stays fully
	equivalent for anyone who can't drag.
-->
<script lang="ts">
	import type { CameraPose } from '$lib/utils/cameraShot';

	export let pose: CameraPose;
	export let onPose: (pose: CameraPose) => void;
	/** Live names of the quantized shots (e.g. "Worm's-eye · Front · Medium"). */
	export let caption = '';

	const CENTER = 100;
	const AZ_STEP = 5;
	const EL_STEP = 5;
	const EL_MIN = -30;
	const EL_MAX = 90;
	const DIST_MIN = 1;
	const DIST_MAX = 9;
	const ROLL_MIN = -45;
	const ROLL_MAX = 45;

	// Stylized bust in model space (Y up, +Z is the front the camera faces at az 0).
	const HEAD = { x: 0, y: 1.5, z: 0 };
	const NOSE = { x: 0, y: 1.44, z: 0.4 };
	const EYE_L = { x: -0.13, y: 1.58, z: 0.3 };
	const EYE_R = { x: 0.13, y: 1.58, z: 0.3 };
	const NECK = { x: 0, y: 1.15, z: 0 };
	const SHOULDER_L = { x: -0.6, y: 1.05, z: 0 };
	const SHOULDER_R = { x: 0.6, y: 1.05, z: 0 };
	const HIP_L = { x: -0.34, y: 0.15, z: 0 };
	const HIP_R = { x: 0.34, y: 0.15, z: 0 };
	const FOOT_L = { x: -0.28, y: -0.85, z: 0 };
	const FOOT_R = { x: 0.28, y: -0.85, z: 0 };
	const HEAD_R = 0.34;

	const EDGES: Array<[typeof HEAD, typeof HEAD]> = [
		[NECK, SHOULDER_L],
		[NECK, SHOULDER_R],
		[SHOULDER_L, SHOULDER_R],
		[SHOULDER_L, HIP_L],
		[SHOULDER_R, HIP_R],
		[HIP_L, HIP_R],
		[HIP_L, FOOT_L],
		[HIP_R, FOOT_R],
		[NECK, HEAD]
	];

	function clamp(v: number, lo: number, hi: number): number {
		return Math.max(lo, Math.min(hi, v));
	}

	function wrapAzimuth(v: number): number {
		return ((v % 360) + 360) % 360;
	}

	// Rotate a model point by the camera orbit and orthographically project it.
	// The figure sits ~0.5 above the model origin, so shift down to centre it.
	// `azimuth`/`elevation` arrive as ARGUMENTS: a `$:` statement only tracks
	// identifiers in its own expression tree, so reads hidden inside this
	// function would freeze the projection at first render (the documented
	// $:-function-call trap).
	function project(p: { x: number; y: number; z: number }, scale: number, azDeg: number, elDeg: number) {
		const az = (azDeg * Math.PI) / 180;
		const el = (elDeg * Math.PI) / 180;
		const cy = Math.cos(az);
		const sy = Math.sin(az);
		const x1 = p.x * cy + p.z * sy;
		const z1 = -p.x * sy + p.z * cy;
		const y1 = p.y - 0.55;
		const ce = Math.cos(el);
		const se = Math.sin(el);
		const y2 = y1 * ce - z1 * se;
		const z2 = y1 * se + z1 * ce;
		return {
			x: CENTER + x1 * scale,
			y: CENTER - y2 * scale,
			depth: z2
		};
	}

	$: scale = clamp(230 / pose.distance, 22, 92);
	$: head = project(HEAD, scale, pose.azimuth, pose.elevation);
	$: nose = project(NOSE, scale, pose.azimuth, pose.elevation);
	$: eyeL = project(EYE_L, scale, pose.azimuth, pose.elevation);
	$: eyeR = project(EYE_R, scale, pose.azimuth, pose.elevation);
	// A face feature is visible when it sits in the camera-facing hemisphere of
	// the head — relative to the head's own depth, NOT absolute depth: at
	// worm's-eye front angles the nose's absolute depth dips negative while the
	// face is still squarely toward the camera, and culling on >= 0 erased the
	// only cue that distinguishes a from-below view from a from-above one.
	$: faceVisible = nose.depth >= head.depth;
	$: edges = EDGES.map(([a, b]) => {
		const pa = project(a, scale, pose.azimuth, pose.elevation);
		const pb = project(b, scale, pose.azimuth, pose.elevation);
		return { x1: pa.x, y1: pa.y, x2: pb.x, y2: pb.y };
	});
	// Ground ellipse at the feet plane, foreshortened by elevation.
	$: groundRy = Math.max(3, Math.abs(Math.sin((pose.elevation * Math.PI) / 180)) * 18 + 4);
	$: groundCy = project({ x: 0, y: -0.85, z: 0 }, scale, pose.azimuth, pose.elevation).y;

	// --- Drag ------------------------------------------------------------------
	let dragging = false;
	let lastX = 0;
	let lastY = 0;

	function onPointerDown(event: PointerEvent) {
		dragging = true;
		lastX = event.clientX;
		lastY = event.clientY;
		(event.currentTarget as Element).setPointerCapture(event.pointerId);
	}

	function onPointerMove(event: PointerEvent) {
		if (!dragging) return;
		const dx = event.clientX - lastX;
		const dy = event.clientY - lastY;
		lastX = event.clientX;
		lastY = event.clientY;
		// Standard orbit convention (three.js/Sketchfab): dragging DOWN pulls the
		// scene down and raises the camera over the subject's head.
		onPose({
			...pose,
			azimuth: wrapAzimuth(pose.azimuth + dx * 0.7),
			elevation: clamp(pose.elevation + dy * 0.6, EL_MIN, EL_MAX)
		});
	}

	function onPointerUp(event: PointerEvent) {
		dragging = false;
		try {
			(event.currentTarget as Element).releasePointerCapture(event.pointerId);
		} catch {
			// capture may already be gone
		}
	}

	function onWheel(event: WheelEvent) {
		event.preventDefault();
		onPose({ ...pose, distance: clamp(pose.distance + event.deltaY * 0.005, DIST_MIN, DIST_MAX) });
	}

	function onKeydown(event: KeyboardEvent) {
		let handled = true;
		if (event.key === 'ArrowLeft') onPose({ ...pose, azimuth: wrapAzimuth(pose.azimuth - AZ_STEP) });
		else if (event.key === 'ArrowRight') onPose({ ...pose, azimuth: wrapAzimuth(pose.azimuth + AZ_STEP) });
		else if (event.key === 'ArrowUp') onPose({ ...pose, elevation: clamp(pose.elevation + EL_STEP, EL_MIN, EL_MAX) });
		else if (event.key === 'ArrowDown') onPose({ ...pose, elevation: clamp(pose.elevation - EL_STEP, EL_MIN, EL_MAX) });
		else handled = false;
		if (handled) event.preventDefault();
	}

	function setDistance(event: Event) {
		onPose({ ...pose, distance: parseFloat((event.target as HTMLInputElement).value) });
	}

	function setRoll(event: Event) {
		onPose({ ...pose, roll: parseFloat((event.target as HTMLInputElement).value) });
	}
</script>

<div class="orbit">
	<!-- svelte-ignore a11y-no-noninteractive-tabindex a11y_no_noninteractive_element_interactions -->
	<svg
		class="stage"
		viewBox="0 0 200 200"
		role="application"
		tabindex="0"
		aria-label={`Camera orbit — drag to rotate, arrow keys to nudge. ${caption ? caption + '. ' : ''}Azimuth ${Math.round(pose.azimuth)} degrees, elevation ${Math.round(pose.elevation)} degrees`}
		on:pointerdown={onPointerDown}
		on:pointermove={onPointerMove}
		on:pointerup={onPointerUp}
		on:pointercancel={onPointerUp}
		on:wheel={onWheel}
		on:keydown={onKeydown}
	>
		<g transform={`rotate(${pose.roll} ${CENTER} ${CENTER})`}>
			<ellipse class="ground" cx={CENTER} cy={groundCy} rx={Math.max(6, scale * 0.7)} ry={groundRy} />
			{#each edges as e}
				<line class="bone" x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} />
			{/each}
			<circle class="head" cx={head.x} cy={head.y} r={HEAD_R * scale} />
			{#if faceVisible}
				<circle class="eye" cx={eyeL.x} cy={eyeL.y} r={Math.max(1.5, scale * 0.05)} />
				<circle class="eye" cx={eyeR.x} cy={eyeR.y} r={Math.max(1.5, scale * 0.05)} />
				<circle class="nose" cx={nose.x} cy={nose.y} r={Math.max(2, scale * 0.08)} />
			{/if}
		</g>
	</svg>

	{#if caption}
		<p class="caption">{caption}</p>
	{/if}

	<div class="controls">
		<label class="ctl">
			<span>Distance</span>
			<input type="range" min={DIST_MIN} max={DIST_MAX} step="0.1" value={pose.distance} on:input={setDistance} />
		</label>
		<label class="ctl">
			<span>Roll</span>
			<input type="range" min={ROLL_MIN} max={ROLL_MAX} step="1" value={pose.roll} on:input={setRoll} />
		</label>
	</div>
	<p class="hint">Drag to orbit · scroll to zoom · arrow keys to nudge</p>
</div>

<style>
	.orbit {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.stage {
		width: 100%;
		max-width: 260px;
		aspect-ratio: 1 / 1;
		margin: 0 auto;
		border: 1px solid rgb(var(--line));
		border-radius: 6px;
		background: rgb(var(--surface-2));
		cursor: grab;
		touch-action: none;
	}
	.stage:active {
		cursor: grabbing;
	}
	.stage:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}
	.ground {
		fill: rgb(var(--line) / 0.5);
	}
	.bone {
		stroke: rgb(var(--fg-muted));
		stroke-width: 2.5;
		stroke-linecap: round;
	}
	.head {
		fill: rgb(var(--surface-3));
		stroke: rgb(var(--fg-muted));
		stroke-width: 2;
	}
	.nose {
		fill: rgb(var(--signal));
	}
	.eye {
		fill: rgb(var(--fg-muted));
	}
	.caption {
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.6875rem;
		letter-spacing: 0.04em;
		color: rgb(var(--fg-muted));
		text-align: center;
	}
	.controls {
		display: flex;
		gap: 0.75rem;
	}
	.ctl {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.ctl span {
		font-size: 0.625rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: rgb(var(--fg-subtle));
	}
	.ctl input[type='range'] {
		width: 100%;
		accent-color: rgb(var(--signal));
	}
	.hint {
		font-size: 0.625rem;
		color: rgb(var(--fg-subtle));
		text-align: center;
	}
</style>
