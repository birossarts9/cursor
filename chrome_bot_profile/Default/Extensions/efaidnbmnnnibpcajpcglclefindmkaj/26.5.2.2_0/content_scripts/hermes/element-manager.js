/*************************************************************************
* ADOBE CONFIDENTIAL
* ___________________
*
*  Copyright 2015 Adobe Systems Incorporated
*  All Rights Reserved.
*
* NOTICE:  All information contained herein is, and remains
* the property of Adobe Systems Incorporated and its suppliers,
* if any.  The intellectual and technical concepts contained
* herein are proprietary to Adobe Systems Incorporated and its
* suppliers and are protected by all applicable intellectual property laws,
* including trade secret and or copyright laws.
* Dissemination of this information or reproduction of this material
* is strictly forbidden unless prior written permission is obtained
* from Adobe Systems Incorporated.
**************************************************************************/
import messageHelpers from"./message-helpers.js";import hermesUtils from"./hermes-utils.js";import{serialize,deserialize}from"./serialization-utils.js";class ElementManager{_ensureHermesId(e){const t=e.getAttribute("data-hermes-id"),r=t||hermesUtils.generateRequestId();return t||e.setAttribute("data-hermes-id",r),r}performElementOperation(e){const{requestId:t,payload:r,_sender:s}=e,{id:n,operation:o,args:i}=r;try{const e=this._executeElementOperation(s,n,o,...i.map(e=>deserialize(e)));hermesUtils.consoleLog("BUILD: element operation result",e),messageHelpers.sendElementOperationResponse(t,s,e)}catch(e){hermesUtils.consoleLog("BUILD: element operation error",e),messageHelpers.sendElementOperationResponse(t,s,null,e.message)}}_accessNestedProperty(e,t){const r=t.split(".");let s=e,n=e;for(let e=0;e<r.length;e+=1){const o=r[e];if(!(o in n)){if(e===r.length-1)return{value:void 0,parent:s,lastKey:o};throw hermesUtils.consoleLog("BUILD: property/operation not found",t),new Error(`Property/Operation ${t} not found`)}s=n,n=n[o]}return{value:n,parent:s,lastKey:r.at(-1)}}_executeElementOperation(e,t,r,...s){hermesUtils.consoleLog("BUILD: performElementOperation",t,r,s);const n=document.querySelector(`[data-hermes-id="${t}"]`);if(!n||!n.isConnected)throw hermesUtils.consoleLog("BUILD: element not found",t),new Error(`Element with id ${t} not found or is not connected to DOM`);if("refreshElement"===r)return hermesUtils.consoleLog("BUILD: refreshElement",n),serialize(n);if("addEventListener"===r){const[t,r]=s;return void n.addEventListener(t,t=>{hermesUtils.consoleLog("BUILD: event listener callback",t),messageHelpers.sendDOMEventMessage(r,e,serialize(t))})}if("setAsync"===r){const[e,t]=s;if(!e)throw new Error("Property name is required");const{parent:r,lastKey:o}=this._accessNestedProperty(n,e);return r[o]=t,!0}const{value:o,parent:i}=this._accessNestedProperty(n,r);if("function"==typeof o){hermesUtils.consoleLog("BUILD: function found",r);const e=o.call(i,...s);return hermesUtils.consoleLog("BUILD ELEMENT_OP: function result",e),serialize(e)}return serialize(o)}}export default new ElementManager;