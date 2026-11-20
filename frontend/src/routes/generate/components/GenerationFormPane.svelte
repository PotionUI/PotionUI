<script lang="ts">
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import FormPane from '$lib/components/generation/GenerationFormPane.svelte';
	import type { GenerationTab } from '$lib/types/tabs';

	let {
		formRef = $bindable(undefined),
		...rest
	}: {
		tab: GenerationTab;
		onFormDataChange: (data: Record<string, unknown>) => void;
		formRef?: DynamicForm | undefined;
		videoDirectorActive?: boolean;
	} = $props();
</script>

<FormPane bind:formRef {...rest} />
