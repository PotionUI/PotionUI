import type { LimitRow } from '$lib/plans/meApi';
import { dailyRemaining } from './compareLimits';
import { effectiveAxes } from './axisValues';
import type { CompareConfig } from './types';

export function largeGridConfirmCopy(
	config: Pick<CompareConfig, 'x' | 'y'>,
	count: number,
	estimate: string,
	limitRows: LimitRow[]
): { title: string; message: string; confirmLabel: string } {
	const { cols, rows } = effectiveAxes(config);
	const shape = cols ? (rows ? `${cols.values.length} × ${rows.values.length}` : `${cols.values.length}`) : `${count}`;
	const time = estimate === '-' ? '' : ` and will take ${estimate} on this backend`;
	const remaining = dailyRemaining(limitRows);
	const limitLine = remaining === null ? '' : `\nIt counts as ${count} against your daily limit (${remaining} left today).`;
	return {
		title: `Run ${count} generations?`,
		message: `This grid is ${shape}${time}.${limitLine}`,
		confirmLabel: `Run ${count}`
	};
}
