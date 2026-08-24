<script lang="ts">
	import FormField from './FormField.svelte';

	export let children: any[] = [];
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let location = 'container';
	export let allowTitleFallback = false;
	// The structural path of the container rendering these children (e.g. the
	// enclosing section/row/tab), or `undefined` at the schema root. Each
	// structural child appends its own segment below - see SectionField.svelte
	// / sectionState.ts for what consumes the result.
	export let fieldPath: string | undefined = undefined;

	const structuralTypes = new Set(['row', 'accordion', 'group', 'tabs', 'section', 'gate']);
	const presentationalTypes = new Set(['alert', 'markdown', 'header']);

	function slugify(text: string | null | undefined): string {
		return (text || '')
			.toString()
			.toLowerCase()
			.trim()
			.replace(/\s+/g, '_')
			.replace(/[^\w-]+/g, '')
			.replace(/--+/g, '_');
	}

	function childFieldPath(child: any, index: number): string {
		const segment = child.name || slugify(child.title || child.label) || String(index);
		return fieldPath ? `${fieldPath}/${segment}` : segment;
	}

	// NOTE: `childName` reads prop `allowTitleFallback` via closure but is called
	// from `{@const}` below — that compiles to an untracked read (Svelte 5 legacy
	// mode), so this stays safe ONLY as long as `allowTitleFallback` is never bound
	// to a value that can change after this component mounts (today its one caller,
	// RowField.svelte, passes a hardcoded `true`). If a future caller passes a
	// reactive expression instead, per-child names will freeze at the first-rendered
	// value.
	function childName(child: any): string {
		return child.name || (allowTitleFallback ? slugify(child.title) : '');
	}
</script>

{#snippet renderChild(child: any, index: number)}
	{@const fieldName = childName(child)}
	{#if structuralTypes.has(child.type)}
		<FormField name={null} config={child} {value} {onChange} {onOriginChange} {onMaskChange} fieldPath={childFieldPath(child, index)} />
	{:else if presentationalTypes.has(child.type)}
		<FormField name={fieldName || null} config={child} value={fieldName ? value?.[fieldName] : undefined} {onChange} {onOriginChange} {onMaskChange} />
	{:else if fieldName}
		<FormField name={fieldName} config={child} value={value?.[fieldName]} {onChange} {onOriginChange} {onMaskChange} />
	{:else}
		<div class="text-xs text-danger">A field inside this {location} is missing a name.</div>
	{/if}
{/snippet}

{#each children as child, index}
	{@render renderChild(child, index)}
{/each}
