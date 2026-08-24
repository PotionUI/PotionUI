import { describe, expect, it } from 'vitest';
import {
	applyPoseToSelection,
	composedPhrase,
	findShotByKey,
	poseSelection,
	poseToShots,
	shotSlot,
	toggleShot,
	type CameraCategory,
	type CameraPose
} from './cameraShot';

function shot(key: string, label: string, phrase: string) {
	return { key, label, default_phrase: phrase, phrase, overridden: false };
}

const catalog: CameraCategory[] = [
	{
		key: 'angle',
		label: 'Angle',
		shots: [
			shot('eye_level', 'Eye level', 'eye-level shot'),
			shot('low_angle', 'Low angle', 'low-angle shot'),
			shot('high_angle', 'High angle', 'high-angle shot'),
			shot('overhead', 'Overhead', 'overhead shot'),
			shot('worms_eye', "Worm's-eye", "worm's-eye view"),
			shot('dutch_angle', 'Dutch angle', 'dutch angle')
		]
	},
	{
		key: 'orientation',
		label: 'Orientation',
		shots: [
			shot('front', 'Front', 'front view'),
			shot('three_quarter', 'Three-quarter', 'three-quarter view'),
			shot('profile', 'Profile', 'side profile view'),
			shot('back', 'Back', 'rear view')
		]
	},
	{
		key: 'distance',
		label: 'Distance',
		shots: [
			shot('extreme_close_up', 'ECU', 'extreme close-up'),
			shot('close_up', 'CU', 'close-up shot'),
			shot('medium_close_up', 'MCU', 'medium close-up'),
			shot('medium', 'Medium', 'medium shot'),
			shot('cowboy', 'Cowboy', 'cowboy shot'),
			shot('full', 'Full', 'full shot'),
			shot('wide', 'Wide', 'wide shot'),
			shot('extreme_wide', 'EWS', 'extreme wide shot')
		]
	},
	{
		key: 'motion',
		label: 'Camera motion',
		shots: [shot('static', 'Static', 'static camera'), shot('pan', 'Pan', 'camera panning')]
	}
];

describe('poseToShots — orientation buckets (azimuth)', () => {
	const orient = (az: number) => poseToShots(az, 0, 4, 0).orientation;

	it('maps front / three-quarter / profile / back by deviation from front', () => {
		expect(orient(0)).toBe('front');
		expect(orient(22.4)).toBe('front');
		expect(orient(22.5)).toBe('three_quarter'); // boundary is exclusive-lower for front
		expect(orient(45)).toBe('three_quarter');
		expect(orient(67.5)).toBe('profile');
		expect(orient(90)).toBe('profile');
		expect(orient(112.5)).toBe('three_quarter'); // back three-quarter
		expect(orient(157.5)).toBe('back');
		expect(orient(180)).toBe('back');
	});

	it('is symmetric for left and right and wraps past 360', () => {
		expect(orient(315)).toBe('three_quarter'); // -45 == 315
		expect(orient(270)).toBe('profile'); // left profile
		expect(orient(360)).toBe('front');
		expect(orient(-90)).toBe('profile');
	});
});

describe('poseToShots — angle buckets (elevation)', () => {
	const angle = (el: number) => poseToShots(0, el, 4, 0).angle;

	it('maps worms_eye / low / eye / high / overhead at exact boundaries', () => {
		expect(angle(-30)).toBe('worms_eye');
		expect(angle(-16)).toBe('worms_eye');
		expect(angle(-15)).toBe('low_angle'); // -15 is not < -15
		expect(angle(-6)).toBe('low_angle');
		expect(angle(-5)).toBe('eye_level');
		expect(angle(0)).toBe('eye_level');
		expect(angle(14)).toBe('eye_level');
		expect(angle(15)).toBe('high_angle');
		expect(angle(59)).toBe('high_angle');
		expect(angle(60)).toBe('overhead');
		expect(angle(90)).toBe('overhead');
	});
});

describe('poseToShots — distance buckets', () => {
	const dist = (d: number) => poseToShots(0, 0, d, 0).distance;

	it('maps the eight distance categories at exact boundaries', () => {
		expect(dist(1)).toBe('extreme_close_up');
		expect(dist(1.5)).toBe('close_up');
		expect(dist(2.5)).toBe('medium_close_up');
		expect(dist(3.5)).toBe('medium');
		expect(dist(4.5)).toBe('cowboy');
		expect(dist(5.5)).toBe('full');
		expect(dist(6.5)).toBe('wide');
		expect(dist(8)).toBe('extreme_wide');
		expect(dist(9)).toBe('extreme_wide');
	});
});

describe('poseToShots — dutch (roll)', () => {
	it('flags dutch at or past ±10 degrees', () => {
		expect(poseToShots(0, 0, 4, 9).dutch).toBe(false);
		expect(poseToShots(0, 0, 4, 10).dutch).toBe(true);
		expect(poseToShots(0, 0, 4, -10).dutch).toBe(true);
		expect(poseToShots(0, 0, 4, 40).dutch).toBe(true);
	});
});

describe('shotSlot', () => {
	it('separates dutch_angle from the other angle shots', () => {
		expect(shotSlot(catalog, 'low_angle')).toBe('angle');
		expect(shotSlot(catalog, 'dutch_angle')).toBe('dutch');
		expect(shotSlot(catalog, 'profile')).toBe('orientation');
		expect(shotSlot(catalog, 'static')).toBe('motion');
		expect(shotSlot(catalog, 'nope')).toBeNull();
	});
});

describe('toggleShot', () => {
	it('adds, then replaces within the same slot', () => {
		let sel = toggleShot([], 'low_angle', catalog);
		expect(sel).toEqual(['low_angle']);
		sel = toggleShot(sel, 'high_angle', catalog);
		expect(sel).toEqual(['high_angle']); // replaced within the angle slot
	});

	it('toggles a shot off when clicked again', () => {
		const sel = toggleShot(['profile'], 'profile', catalog);
		expect(sel).toEqual([]);
	});

	it('lets dutch coexist with a directional angle', () => {
		let sel = toggleShot(['low_angle'], 'dutch_angle', catalog);
		expect(sel).toEqual(['low_angle', 'dutch_angle']);
		// A second directional angle still replaces only the angle slot, keeping dutch.
		sel = toggleShot(sel, 'high_angle', catalog);
		expect(sel.sort()).toEqual(['dutch_angle', 'high_angle']);
	});
});

describe('poseSelection', () => {
	it('includes angle, orientation, distance and dutch when present', () => {
		const pose: CameraPose = { azimuth: 90, elevation: -10, distance: 2, roll: 20 };
		expect(poseSelection(pose, catalog).sort()).toEqual(
			['close_up', 'dutch_angle', 'low_angle', 'profile'].sort()
		);
	});

	it('omits categories the catalog does not show', () => {
		const imageOnly = catalog.filter((c) => c.key !== 'orientation');
		const pose: CameraPose = { azimuth: 90, elevation: 0, distance: 4, roll: 0 };
		expect(poseSelection(pose, imageOnly)).not.toContain('profile');
	});
});

describe('applyPoseToSelection', () => {
	it('replaces orbit slots but keeps a manual motion pick', () => {
		const pose: CameraPose = { azimuth: 0, elevation: 0, distance: 4, roll: 0 };
		const result = applyPoseToSelection(['static', 'profile'], pose, catalog);
		expect(result).toContain('static'); // motion survives
		expect(result).toContain('front'); // orientation replaced by the pose
		expect(result).not.toContain('profile');
	});
});

describe('composedPhrase', () => {
	it('joins selected phrases in catalog order', () => {
		const sel = ['medium', 'low_angle', 'profile'];
		expect(composedPhrase(catalog, sel)).toBe('low-angle shot, side profile view, medium shot');
	});

	it('is empty for an empty selection', () => {
		expect(composedPhrase(catalog, [])).toBe('');
	});
});

describe('findShotByKey', () => {
	it('finds a shot and returns null for unknown / empty', () => {
		expect(findShotByKey(catalog, 'profile')?.label).toBe('Profile');
		expect(findShotByKey(catalog, 'nope')).toBeNull();
		expect(findShotByKey(catalog, null)).toBeNull();
		expect(findShotByKey([], 'profile')).toBeNull();
	});
});
