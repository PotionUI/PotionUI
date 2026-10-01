export interface Command {
	label: string;
	bytes: number;
	undo(): void;
	redo(): void;
}

type Listener = () => void;

export class History {
	private commands: Command[] = [];
	private applied = 0;
	private evicted = 0;
	private savedPosition = 0;
	private total = 0;
	private listeners = new Set<Listener>();

	constructor(private readonly budgetBytes: number) {}

	get canUndo(): boolean {
		return this.applied > 0;
	}

	get canRedo(): boolean {
		return this.applied < this.commands.length;
	}

	get undoLabel(): string | null {
		return this.applied > 0 ? this.commands[this.applied - 1].label : null;
	}

	get redoLabel(): string | null {
		return this.applied < this.commands.length ? this.commands[this.applied].label : null;
	}

	get dirty(): boolean {
		return this.evicted + this.applied !== this.savedPosition;
	}

	get bytes(): number {
		return this.total;
	}

	get length(): number {
		return this.commands.length;
	}

	push(command: Command): void {
		const dropped = this.commands.splice(this.applied);
		for (const gone of dropped) this.total -= gone.bytes;
		this.commands.push(command);
		this.total += command.bytes;
		this.applied = this.commands.length;

		while (this.commands.length > 1 && this.total > this.budgetBytes) {
			const oldest = this.commands.shift();
			if (!oldest) break;
			this.total -= oldest.bytes;
			this.applied -= 1;
			this.evicted += 1;
		}
		this.emit();
	}

	undo(): Command | null {
		if (!this.canUndo) return null;
		const command = this.commands[this.applied - 1];
		command.undo();
		this.applied -= 1;
		this.emit();
		return command;
	}

	redo(): Command | null {
		if (!this.canRedo) return null;
		const command = this.commands[this.applied];
		command.redo();
		this.applied += 1;
		this.emit();
		return command;
	}

	clear(): void {
		this.commands = [];
		this.applied = 0;
		this.evicted = 0;
		this.savedPosition = 0;
		this.total = 0;
		this.emit();
	}

	markSaved(): void {
		this.savedPosition = this.evicted + this.applied;
		this.emit();
	}

	subscribe(listener: Listener): () => void {
		this.listeners.add(listener);
		return () => {
			this.listeners.delete(listener);
		};
	}

	private emit(): void {
		for (const listener of this.listeners) listener();
	}
}
