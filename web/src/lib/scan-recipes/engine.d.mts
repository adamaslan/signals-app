export const ENGINE_VERSION: string;
export function runRecipe(recipe: unknown,input: unknown): {status:string;[key:string]:unknown};
export function listRecipes(catalog: unknown[],filter?:unknown):unknown[];
