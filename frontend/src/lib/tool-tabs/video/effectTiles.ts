import { effectDefaults, effectGroups, lookOptions, type EffectTool } from '$lib/effects/catalog';
import type { VideoParams } from './videoSteps';

export interface EffectTile {
	id: string;
	label: string;
	group: string;
	effect: EffectTool;
	values: Record<string, unknown>;
	lookParam: string | null;
	lookValue: string | null;
	renderable: boolean;
}

export function thumbRenderable(effect: EffectTool): boolean {
	return !!effect.block.shader && !effect.block.analysis && effect.block.textures.every((texture) => texture.kind === 'lut3d');
}

function capitalise(text: string): string {
	return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

export function effectTiles(effects: readonly EffectTool[]): EffectTile[] {
	const looks: EffectTile[] = [];
	const plain: EffectTile[] = [];
	const rank = new Map<string, number>();
	effectGroups(effects).forEach((group, index) => group.effects.forEach((effect) => rank.set(effect.id, index)));
	const ordered = [...effects].sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0));
	for (const effect of ordered) {
		const lookParam = effect.params.find((param) => param.type === 'look');
		const renderable = thumbRenderable(effect);
		const defaults = effectDefaults(effect);
		if (lookParam) {
			for (const option of lookOptions(lookParam)) {
				if (!option.value) continue;
				looks.push({
					id: `${effect.id}:${option.value}`,
					label: option.label,
					group: capitalise(option.group) || effect.label,
					effect,
					values: { ...defaults, [lookParam.name]: option.value },
					lookParam: lookParam.name,
					lookValue: option.value,
					renderable
				});
			}
			continue;
		}
		const group = effectGroups([effect])[0];
		plain.push({ id: effect.id, label: effect.label, group: group.label, effect, values: defaults, lookParam: null, lookValue: null, renderable });
	}
	return [...looks, ...plain];
}

export function tileMatches(tile: EffectTile, tool: string | null | undefined, params: VideoParams | null | undefined): boolean {
	if (!tool || tool !== tile.effect.id) return false;
	if (!tile.lookParam) return true;
	return !!params && params[tile.lookParam] === tile.lookValue;
}

export function tileById(tiles: readonly EffectTile[], id: string | null): EffectTile | null {
	return id ? (tiles.find((tile) => tile.id === id) ?? null) : null;
}
