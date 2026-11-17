export interface AreaState {
	marking: boolean;
	drawn: boolean;
	savedUsable: boolean;
}

export function areaIsMarked(state: AreaState): boolean {
	return state.marking ? state.drawn : state.savedUsable;
}

export async function landThenSend(land: () => Promise<boolean>, send: () => Promise<void>, onLandFailed: () => void): Promise<boolean> {
	if (!(await land())) {
		onLandFailed();
		return false;
	}
	await send();
	return true;
}
