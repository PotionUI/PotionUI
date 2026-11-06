import { clampCropRect, fitAspect, mirrorCropRect, rotateCropRect, turn } from '$lib/media/editors/cropGeometry';
import type { CropParams } from './steps';

export const ORIENTATION = [
	{ key: 'rotate-left', label: 'Rotate left', icon: 'editor-rotate-left' },
	{ key: 'rotate-right', label: 'Rotate right', icon: 'editor-rotate-right' },
	{ key: 'flip-h', label: 'Flip horizontal', icon: 'flip' },
	{ key: 'flip-v', label: 'Flip vertical', icon: 'editor-flip-v' }
] as const;

export type OrientationKey = (typeof ORIENTATION)[number]['key'];

export function withAspect(params: CropParams, key: string, ratio: number | null, frame: { width: number; height: number }): CropParams {
	const aspect = frame.height > 0 ? frame.width / frame.height : 1;
	return { aspect: key, plan: ratio ? { ...params.plan, rect: fitAspect(params.plan.rect, ratio, aspect) } : params.plan };
}

export type RectField = 'x' | 'y' | 'width' | 'height';

export function rectInPixels(params: CropParams, frame: { width: number; height: number }): Record<RectField, number> {
	const { rect } = params.plan;
	return { x: Math.round(rect.x * frame.width), y: Math.round(rect.y * frame.height), width: Math.round(rect.width * frame.width), height: Math.round(rect.height * frame.height) };
}

export function withRectField(params: CropParams, frame: { width: number; height: number }, field: RectField, pixels: number): CropParams {
	if (!(frame.width > 0) || !(frame.height > 0) || !Number.isFinite(pixels)) return params;
	const axis = field === 'x' || field === 'width' ? frame.width : frame.height;
	const current = params.plan.rect;
	const next = { ...current, [field]: pixels / axis };
	if (field === 'x') next.x = Math.min(next.x, 1 - current.width);
	if (field === 'y') next.y = Math.min(next.y, 1 - current.height);
	if (field === 'width') next.width = Math.min(next.width, 1 - current.x);
	if (field === 'height') next.height = Math.min(next.height, 1 - current.y);
	return { aspect: 'free', plan: { ...params.plan, rect: clampCropRect(next) } };
}

export function withOrientation(params: CropParams, key: OrientationKey): CropParams {
	const plan = params.plan;
	if (key === 'rotate-left' || key === 'rotate-right') {
		const quarters = key === 'rotate-right' ? 1 : -1;
		return { ...params, plan: { ...plan, rotation: turn(plan.rotation, quarters), rect: rotateCropRect(plan.rect, quarters) } };
	}
	const axis = key === 'flip-h' ? 'horizontal' : 'vertical';
	return {
		...params,
		plan: {
			...plan,
			flipHorizontal: axis === 'horizontal' ? !plan.flipHorizontal : plan.flipHorizontal,
			flipVertical: axis === 'vertical' ? !plan.flipVertical : plan.flipVertical,
			rect: mirrorCropRect(plan.rect, axis)
		}
	};
}
