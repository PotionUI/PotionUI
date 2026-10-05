import { createRawSnippet, mount, unmount, type Snippet } from 'svelte';
import type { LimitKindDescriptor, UserUsageRow } from '$lib/plans/types';
import UsageBar from './UsageBar.svelte';

export function usageColumnCell<Row extends { id: string }>(
	kind: LimitKindDescriptor,
	getUsage: () => ReadonlyMap<string, UserUsageRow>
): Snippet<[Row]> {
	return createRawSnippet((getRow: () => Row) => ({
		render: () => '<div class="min-w-0"></div>',
		setup(node: Element) {
			const component = mount(UsageBar, {
				target: node,
				props: {
					kind,
					compact: true,
					get used() {
						return getUsage().get(getRow().id)?.limits.find((l) => l.kind === kind.key)?.used ?? 0;
					},
					get limit() {
						return getUsage().get(getRow().id)?.limits.find((l) => l.kind === kind.key)?.limit ?? null;
					}
				}
			});
			return () => {
				void unmount(component);
			};
		}
	}));
}
