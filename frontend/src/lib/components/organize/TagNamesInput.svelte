<script lang="ts">
	import { IconButton } from '$lib/components/ui';
	import type { OrganizeOption } from '$lib/types/organize';

	let {
		value,
		placeholder = 'Add a tag',
		loadOptions,
		onchange
	}: {
		value: string[];
		placeholder?: string;
		loadOptions?: (query: string) => Promise<OrganizeOption[]>;
		onchange: (next: string[]) => void;
	} = $props();

	let draft = $state('');
	let suggestions = $state<OrganizeOption[]>([]);
	let focused = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	const visible = $derived(
		suggestions.filter((s) => !value.includes(s.value)).slice(0, 8)
	);

	function scheduleLoad(query: string) {
		clearTimeout(timer);
		if (!loadOptions) return;
		timer = setTimeout(async () => {
			try {
				suggestions = await loadOptions(query);
			} catch {
				suggestions = [];
			}
		}, 200);
	}

	function add(raw: string) {
		const name = raw.trim();
		draft = '';
		if (!name || value.includes(name)) return;
		onchange([...value, name]);
	}

	function remove(name: string) {
		onchange(value.filter((entry) => entry !== name));
	}

	function handleKey(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ',') {
			event.preventDefault();
			add(draft);
		} else if (event.key === 'Backspace' && !draft && value.length > 0) {
			onchange(value.slice(0, -1));
		}
	}
</script>

<div class="relative">
	<div class="input flex min-h-9 flex-wrap items-center gap-1.5 py-1" data-testid="tag-names-input">
		{#each value as name (name)}
			<span class="inline-flex h-6 items-center gap-1 rounded border border-line-strong bg-surface-3 pl-2 pr-0.5 text-xs text-fg">
				{name}
				<IconButton icon="close" label="Remove {name}" size="xs" onclick={() => remove(name)} />
			</span>
		{/each}
		<input
			type="text"
			class="min-w-[8rem] flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-fg-subtle"
			{placeholder}
			aria-label={placeholder}
			bind:value={draft}
			onkeydown={handleKey}
			oninput={() => scheduleLoad(draft)}
			onfocus={() => {
				focused = true;
				scheduleLoad(draft);
			}}
			onblur={() => {
				focused = false;
				add(draft);
			}}
		/>
	</div>
	{#if focused && visible.length > 0}
		<div class="absolute left-0 right-0 top-full z-30 mt-1 overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating" role="listbox">
			{#each visible as option (option.value)}
				<button
					type="button"
					role="option"
					aria-selected="false"
					class="block w-full px-3 py-1.5 text-left text-sm text-fg hover:bg-surface-3"
					onmousedown={(event) => {
						event.preventDefault();
						add(option.value);
					}}
				>
					{option.label}
				</button>
			{/each}
		</div>
	{/if}
</div>
