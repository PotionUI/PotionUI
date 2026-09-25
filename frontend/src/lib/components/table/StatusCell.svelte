<script lang="ts">
	type Tone = 'success' | 'danger' | 'warning' | 'info' | 'signal' | 'muted';

	let {
		tone = 'muted',
		label,
		class: className = ''
	}: {
		tone?: Tone;
		label: string;
		class?: string;
	} = $props();

	const dotClasses: Record<Tone, string> = {
		success: 'bg-success',
		danger: 'bg-danger',
		warning: 'bg-warning',
		info: 'bg-info',
		signal: 'bg-signal',
		muted: 'bg-fg-subtle'
	};

	const textClasses: Record<Tone, string> = {
		success: 'text-success',
		danger: 'text-danger',
		warning: 'text-warning',
		info: 'text-info',
		signal: 'text-signal',
		muted: 'text-fg-muted'
	};
</script>

<span class="inline-flex items-center gap-1.5 font-mono text-xs {textClasses[tone]} {className}">
	<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full {dotClasses[tone]}" aria-hidden="true"></span>
	{label}
</span>
