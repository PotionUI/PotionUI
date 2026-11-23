export type HeaderActionKey = 'styles' | 'variables' | 'library';

export interface HeaderStage {
	wrap: boolean;
	icons: HeaderActionKey[];
	folded: HeaderActionKey[];
}

export interface HeaderStageInput {
	actions: HeaderActionKey[];
	tabs: boolean;
	compact?: boolean;
}

const FOLD_ORDER: HeaderActionKey[] = ['variables', 'styles'];

export function headerStages({ actions, tabs, compact = false }: HeaderStageInput): HeaderStage[] {
	const present: HeaderActionKey[] = actions.filter((key) => key !== 'library');
	const everything: HeaderActionKey[] = [...present, 'library'];
	const stages: HeaderStage[] = [{ wrap: false, icons: [], folded: [] }];
	if (tabs) stages.push({ wrap: true, icons: [], folded: [] });
	if (present.length > 0) stages.push({ wrap: tabs, icons: [...present], folded: [] });
	stages.push({ wrap: tabs, icons: everything, folded: [] });
	const folded: HeaderActionKey[] = [];
	for (const key of FOLD_ORDER) {
		if (!present.includes(key)) continue;
		folded.push(key);
		stages.push({ wrap: tabs, icons: everything, folded: [...folded] });
	}
	const start = compact ? stages.findIndex((stage) => stage.icons.length > 0) : 0;
	return start > 0 ? stages.slice(start) : stages;
}

export function pickStage<T>(stages: T[], apply: (stage: T) => void, fits: () => boolean): number {
	for (let i = 0; i < stages.length; i++) {
		apply(stages[i]);
		if (fits()) return i;
	}
	return stages.length - 1;
}

const MIN_STRIP_WIDTH = 220;

export interface HeaderMeasure {
	scrollWidth: number;
	clientWidth: number;
	wrapped: boolean;
	strip: { clientWidth: number; scrollWidth: number } | null;
}

export function headerFits(measure: HeaderMeasure): boolean {
	if (measure.scrollWidth > measure.clientWidth + 1) return false;
	if (measure.strip && !measure.wrapped && measure.strip.clientWidth < Math.min(measure.strip.scrollWidth, MIN_STRIP_WIDTH)) {
		return false;
	}
	return true;
}

export function stageSignature(stage: HeaderStage): string {
	return `${stage.wrap ? 'wrap' : 'row'}|${stage.icons.join(' ')}|${stage.folded.join(' ')}`;
}

export function stagesKey(stages: HeaderStage[]): string {
	return stages.map(stageSignature).join('/');
}

export interface HeaderFitParams {
	stages: HeaderStage[];
	onChange?: (stage: HeaderStage) => void;
}

const STRIP = '[data-header-strip]';
const FITTING = 'is-fitting';

function writeStage(node: HTMLElement, stage: HeaderStage) {
	if (stage.wrap) node.dataset.wrap = '1';
	else delete node.dataset.wrap;
	node.dataset.icons = stage.icons.join(' ');
	node.dataset.folded = stage.folded.join(' ');
}

function measureHeader(node: HTMLElement): HeaderMeasure {
	const strip = node.querySelector<HTMLElement>(STRIP);
	return {
		scrollWidth: node.scrollWidth,
		clientWidth: node.clientWidth,
		wrapped: node.dataset.wrap === '1',
		strip: strip ? { clientWidth: strip.clientWidth, scrollWidth: strip.scrollWidth } : null
	};
}

export function headerFit(node: HTMLElement, initial: HeaderFitParams) {
	let params = initial;
	let paramsKey = stagesKey(initial.stages);
	let signature = '';
	let width = -1;
	let destroyed = false;
	const resize =
		typeof ResizeObserver === 'undefined'
			? null
			: new ResizeObserver(() => {
					if (node.clientWidth !== width) compute();
				});
	const mutation = typeof MutationObserver === 'undefined' ? null : new MutationObserver(() => compute());

	function compute() {
		if (destroyed || node.clientWidth === 0 || params.stages.length === 0) return;
		width = node.clientWidth;
		node.classList.add(FITTING);
		let index = 0;
		try {
			index = pickStage(
				params.stages,
				(stage) => writeStage(node, stage),
				() => headerFits(measureHeader(node))
			);
		} finally {
			node.classList.remove(FITTING);
		}
		const stage = params.stages[index];
		const next = stageSignature(stage);
		if (next === signature) return;
		signature = next;
		params.onChange?.(stage);
	}

	resize?.observe(node);
	mutation?.observe(node, { childList: true, subtree: true, characterData: true });
	queueMicrotask(compute);
	void document.fonts?.ready.then(compute);

	return {
		update(next: HeaderFitParams) {
			params = next;
			const nextKey = stagesKey(next.stages);
			if (nextKey === paramsKey) return;
			paramsKey = nextKey;
			queueMicrotask(compute);
		},
		destroy() {
			destroyed = true;
			resize?.disconnect();
			mutation?.disconnect();
		}
	};
}
