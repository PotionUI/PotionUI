import { getContext, setContext } from 'svelte';
import { GENERATE_CONTEXT, type GenerateController } from './controller';

export function provideGenerateController(controller: GenerateController): void {
	setContext(GENERATE_CONTEXT, controller);
}

export function getGenerateController(): GenerateController | null {
	return (getContext(GENERATE_CONTEXT) as GenerateController | undefined) ?? null;
}
