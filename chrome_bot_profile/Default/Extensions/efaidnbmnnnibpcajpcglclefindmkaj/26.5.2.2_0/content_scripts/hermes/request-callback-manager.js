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
import hermesUtils from"./hermes-utils.js";const requestCallbackMap=new Map;class RequestCallbackManager{static has(e){return requestCallbackMap.has(e)}static get(e){return requestCallbackMap.get(e)||null}static registerCallback(e,t,a=!1){if("function"!=typeof t)throw new Error("Callback must be a function");requestCallbackMap.set(e,{callback:t,deleteAfterExecute:a})}static delete(e){return requestCallbackMap.delete(e)}static execute(e,t){const a=requestCallbackMap.get(e);if(!a)return!1;const{callback:r,deleteAfterExecute:l=!1}=a;if(!r)return!1;try{return r(t),l&&requestCallbackMap.delete(e),!0}catch(t){return hermesUtils.consoleLog(`Error executing callback for requestId ${e}:`,t),requestCallbackMap.delete(e),!1}}static clear(){requestCallbackMap.clear()}static size(){return requestCallbackMap.size}static getRequestIds(){return Array.from(requestCallbackMap.keys())}static isEmpty(){return 0===requestCallbackMap.size}static getSnapshot(){const e={};return Array.from(requestCallbackMap.entries()).forEach(([t,{callback:a,deleteAfterExecute:r}])=>{e[t]={callbackType:typeof a,callbackName:a.name||"anonymous",callbackLength:a.length,deleteAfterExecute:r}}),e}}export default RequestCallbackManager;