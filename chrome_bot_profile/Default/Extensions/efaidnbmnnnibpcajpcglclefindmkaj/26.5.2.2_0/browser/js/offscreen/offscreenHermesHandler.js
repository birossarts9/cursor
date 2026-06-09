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
import{wrapTask as e}from"./offscreenTaskManager.js";import{logError as r,consoleLog as n,logInfo as s}from"./offscrenUtil.js";const t=new Map;let a=null;const o=chrome.runtime.sendMessage({main_op:"getFloodgateMeta",flag:"dc-cv-hermes-logging",tab:{id:"offscreenTabId"}});export async function removeHermesIframe(e,r){o?.then(e=>{const r=JSON.parse(e);r?.loggingEnabled&&s("Hermes: Removing hermes iframe",{executionSource:"hermes"})}).catch(()=>{});const n=r.tab.id,a=t.get(n);a?.remove(),t.delete(n)}export async function createHermesIframe(e,r){const m=new URL(r.url).hostname;o?.then(e=>{const r=JSON.parse(e);r?.loggingEnabled&&s("Hermes: Creating hermes iframe",{domain:m,executionSource:"hermes"})}).catch(()=>{}),n("createHermesIframe",e,r);const i=r.tab.id;t.has(i)&&removeHermesIframe(e,r);const c=window.document;a=e.iframeURL;const f=new URL(a),d=c.createElement("iframe");d.setAttribute("id",`hermes-iframe-${Math.random().toString(36).substring(2,15)}`),d.setAttribute("src",f.toString());c.getElementById("cdn-re-iframe").appendChild(d),t.set(i,d)}export async function sendMessageToHermesIframe(e,s){n("sendMessageToHermesIframe",e,s);const a=s.tab.id;if(!t.has(a))return void r("Hermes iframe not found");const o=t.get(a);o.contentWindow.postMessage({...e},new URL(o.src).origin)}export function handleHermesEvent(e){if(n("handleHermesEvent",e),!a||e.origin!==new URL(a).origin)return;const r=e.data;if("hermes"!==r?.messageType)return;const s=e.source,o=t.entries(),[m]=Array.from(o).find(([,e])=>e.contentWindow===s);m&&chrome.runtime.sendMessage({...r,main_op:"relayOffscreenMessageToContentScript",target:"background",tab:{id:m}})}export function getHermesTaskHandlers(){return{createHermesIframe:e("createHermesIframe",createHermesIframe),removeHermesIframe:e("removeHermesIframe",removeHermesIframe),sendMessageToHermesIframe:e("sendMessageToHermesIframe",sendMessageToHermesIframe)}}