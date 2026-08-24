/**
 * Types, lookup, pose quantization and selection logic for the camera-shot
 * picker. The backend resolves each shot's phrase (preset `vocabulary` override
 * else the built-in default) and embeds the finished catalog in the field schema
 * (see src/features/fields/camera_shot.py), so the client only renders, quantizes
 * the 3D pose to canonical shots, and composes the combined phrase.
 */

export interface CameraShot {
	key: string;
	label: string;
	/** Built-in default phrase (informational). */
	default_phrase: string;
	/** Resolved phrase to insert/copy: preset override, else default. */
	phrase: string;
	/** True when the preset overrode this shot's phrasing. */
	overridden: boolean;
}

export interface CameraCategory {
	key: string;
	label: string;
	shots: CameraShot[];
}

export interface CameraPose {
	/** Camera azimuth around the subject, degrees. 0 = front, increases clockwise. */
	azimuth: number;
	/** Camera elevation, degrees. Negative = below (looking up), positive = above. */
	elevation: number;
	/** Camera distance, abstract units 1 (extreme close) .. 9 (extreme wide). */
	distance: number;
	/** Camera roll, degrees. |roll| past the dutch threshold reads as a dutch angle. */
	roll: number;
}

export interface PoseShots {
	/** Canonical angle key from elevation (worms_eye..overhead). */
	angle: string;
	/** Canonical orientation key from azimuth (front/three_quarter/profile/back). */
	orientation: string;
	/** Canonical distance key. */
	distance: string;
	/** Whether the roll reads as a dutch angle. */
	dutch: boolean;
}

/** Roll magnitude (degrees) at or past which the shot reads as a dutch angle. */
export const DUTCH_ROLL_DEG = 10;

/** Slots the 3D orbit drives; a grid pick in one of these replaces the orbit's. */
const ORBIT_SLOTS = new Set(['angle', 'dutch', 'orientation', 'distance']);

/** Find a shot by its canonical key across every category, or null. */
export function findShotByKey(catalog: CameraCategory[], key: string | null): CameraShot | null {
	if (!key) return null;
	for (const category of catalog || []) {
		const shot = (category.shots || []).find((s) => s.key === key);
		if (shot) return shot;
	}
	return null;
}

/** Signed azimuth's absolute deviation from front (0..180 degrees). */
function frontDeviation(azimuth: number): number {
	const wrapped = ((azimuth % 360) + 360) % 360;
	return wrapped > 180 ? 360 - wrapped : wrapped;
}

function azimuthToOrientation(azimuth: number): string {
	const dev = frontDeviation(azimuth);
	if (dev < 22.5) return 'front';
	if (dev < 67.5) return 'three_quarter';
	if (dev < 112.5) return 'profile';
	if (dev < 157.5) return 'three_quarter';
	return 'back';
}

function elevationToAngle(elevation: number): string {
	if (elevation < -15) return 'worms_eye';
	if (elevation < -5) return 'low_angle';
	if (elevation < 15) return 'eye_level';
	if (elevation < 60) return 'high_angle';
	return 'overhead';
}

function distanceToShot(distance: number): string {
	if (distance < 1.5) return 'extreme_close_up';
	if (distance < 2.5) return 'close_up';
	if (distance < 3.5) return 'medium_close_up';
	if (distance < 4.5) return 'medium';
	if (distance < 5.5) return 'cowboy';
	if (distance < 6.5) return 'full';
	if (distance < 8) return 'wide';
	return 'extreme_wide';
}

/** Quantize a continuous camera pose to the nearest canonical shots. Pure. */
export function poseToShots(
	azimuth: number,
	elevation: number,
	distance: number,
	roll: number
): PoseShots {
	return {
		angle: elevationToAngle(elevation),
		orientation: azimuthToOrientation(azimuth),
		distance: distanceToShot(distance),
		dutch: Math.abs(roll) >= DUTCH_ROLL_DEG
	};
}

/** The dedup slot a shot occupies: dutch_angle stands apart from the other angle
 *  shots so a roll-driven dutch can coexist with a directional angle. Returns null
 *  for a key not in the catalog. */
export function shotSlot(catalog: CameraCategory[], key: string): string | null {
	if (key === 'dutch_angle') return 'dutch';
	for (const category of catalog || []) {
		if ((category.shots || []).some((s) => s.key === key)) return category.key;
	}
	return null;
}

/** Toggle a shot in the selection, keeping at most one shot per slot. */
export function toggleShot(selection: string[], key: string, catalog: CameraCategory[]): string[] {
	if (selection.includes(key)) {
		return selection.filter((k) => k !== key);
	}
	const slot = shotSlot(catalog, key);
	const kept = selection.filter((k) => shotSlot(catalog, k) !== slot);
	return [...kept, key];
}

/** The shot keys a pose maps to, limited to categories the catalog actually shows. */
export function poseSelection(pose: CameraPose, catalog: CameraCategory[]): string[] {
	const present = new Set((catalog || []).map((c) => c.key));
	const shots = poseToShots(pose.azimuth, pose.elevation, pose.distance, pose.roll);
	const keys: string[] = [];
	if (present.has('angle')) keys.push(shots.angle);
	if (present.has('orientation')) keys.push(shots.orientation);
	if (present.has('distance')) keys.push(shots.distance);
	if (present.has('angle') && shots.dutch) keys.push('dutch_angle');
	return keys.filter((k) => findShotByKey(catalog, k) !== null);
}

/** Apply a pose to the selection: replace the orbit-driven slots, keep the rest
 *  (e.g. a manually picked `motion` shot survives while the camera moves). */
export function applyPoseToSelection(
	selection: string[],
	pose: CameraPose,
	catalog: CameraCategory[]
): string[] {
	const kept = selection.filter((k) => {
		const slot = shotSlot(catalog, k);
		return slot !== null && !ORBIT_SLOTS.has(slot);
	});
	return [...kept, ...poseSelection(pose, catalog)];
}

/** The combined phrase for the current selection, ordered by catalog position. */
export function composedPhrase(catalog: CameraCategory[], selection: string[]): string {
	const chosen = new Set(selection);
	const parts: string[] = [];
	for (const category of catalog || []) {
		for (const shot of category.shots || []) {
			if (chosen.has(shot.key)) parts.push(shot.phrase);
		}
	}
	return parts.join(', ');
}
