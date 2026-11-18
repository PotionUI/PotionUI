export const DOCK_STEP = {
	preset: 'ctx-step flex min-w-0 flex-[0_1_auto] shrink-[1] pl-0 pr-3',
	session: 'ctx-step flex min-w-0 flex-[0_1_auto] shrink-[3] px-3',
	mode: 'ctx-step flex min-w-0 flex-[0_1_auto] shrink-[6] px-3',
	formula: 'ctx-step flex min-w-0 flex-[0_1_auto] shrink-[6] px-3'
} as const;

export const DOCK_CELL = {
	preset: 'min-w-[88px] max-w-[240px]',
	mode: 'min-w-[72px] max-w-[240px]',
	formula: 'min-w-[72px] max-w-[160px]',
	session: 'min-w-[132px] max-w-[360px]'
} as const;

export const DOCK_END = 'dock-end ml-auto flex flex-none shrink-0 items-center gap-3 self-stretch whitespace-nowrap pl-4';

export const DOCK_PICKERS = 'dock-pickers flex min-w-0 flex-[0_1_auto] items-stretch';
