<script lang="ts">
	import { Alert } from '$lib/components/ui';

	export let config: any;

	$: description = config.description || '';
	$: content = config.content || description || '';
	$: title = config.alertTitle || config.title || undefined;

	// Backend's declared FieldConfigSpec is `configuration.variant` (see
	// src/features/fields/alert.py) - map its choices to ui/Alert's set.
	const variantMap: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
		success: 'success',
		warning: 'warning',
		danger: 'danger',
		primary: 'neutral',
		secondary: 'neutral',
		default: 'neutral'
	};
	$: variant = variantMap[config.variant] ?? 'neutral';
</script>

<Alert {variant} {title} live="off" density="compact" class="my-1">{content}</Alert>
