export interface Recipe {id:string;version:string;scope:string;metric:string;horizon:number;view:string;title:string;family:string;unit:string;threshold:number;stress_side:string;question:string;[key:string]:unknown}
declare const catalog:Recipe[];
export default catalog;
