export const SUPPORTED_METHODS: number[];
export const EXTRA_CONTRACTS: Record<number,string[]>;
export function runPullbackMethod(method:unknown,input:unknown): {status:string;[key:string]:unknown};
