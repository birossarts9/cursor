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
import{common as e}from"../common.js";import{util as r}from"../util.js";import{forceResetService as n}from"../force-reset-service.js";import t from"../CacheStore.js";import{loggingApi as o}from"../../common/loggingApi.js";async function s(r="prod"){const o=new t,s=`hermes-version-mapping-${r}`,i=async()=>{const r=e.getHermesVersionMappingUri?.()??null;if(!r)return{};const n=await fetch(r);if(!n.ok)throw new Error(`Hermes version mapping fetch failed: ${n.status}`);const t=function(e){return e?Object.entries(e).reduce((e,[r,n])=>({...e,[r]:n}),{}):{}}(await n.json());return await o.set(s,t),t};n.executeFeature("hermes-version-mapping",i);const c=await o.get(s);if(c)return c;try{return await i()}catch{return{}}}export async function getResolvedHermesCdnUrl(n="prod"){const t=e.getHermesCdnUrl?.()??null;if(!t)return t;if(t.includes("/ephemeral/")||"local-dev"===n)return t;if(!(e.getHermesVersionMappingUri?.()??null))return t;const i=chrome.runtime.getManifest().version;if(!i)return t;try{const e=await s(n);if(!e)return t;const c=function(e,n){const t=Object.keys(n);if(0===t.length)return null;const o=t.filter(n=>r.verCmp(n,e)<=0);if(0===o.length)return null;const s=n[o.reduce((e,n)=>r.verCmp(e,n)>=0?e:n)];return s?.cdn??null}(i,e);if(!c)return o.error({message:"Error getting resolved Hermes CDN URL",extensionVersion:i}),t;const m=new URL(t);return m.pathname=m.pathname.replace(/\/hermes-index\.html$/i,`/${c}/hermes-index.html`),m.toString()}catch(e){return o.error({message:"Error getting resolved Hermes CDN URL",error:e.message,extensionVersion:i}),t}}