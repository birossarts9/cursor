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
import{dcLocalStorage as e}from"../../common/local-storage.js";import{signInUtil as r}from"../../browser/js/viewer/signInUtils.js";const s=new URLSearchParams(window.location.search),n=function(e){if(!e)return null;try{return new URLSearchParams(decodeURIComponent(e))}catch(e){return null}}(s.get("gsp")),o=parseInt(s.get("tabId"),10);if(function(e,r){const s=e.get("pdfspaceupsell")||r?.get("pdfspaceupsell");return"1"===s||"true"===s}(s,n)){const e=function(e,r){let s=e;return r&&Number.isNaN(s)&&(s=parseInt(r.get("tabId"),10)),s}(o,n);Number.isNaN(e)?window.close():function(e){chrome.runtime.sendMessage({main_op:"post_pdfspace_upsell",tabId:e},()=>{chrome.runtime.lastError&&console.error("Error sending post_pdfspace_upsell:",chrome.runtime.lastError.message),window.close()})}(e)}else Number.isNaN(o)?window.close():(t=o,e.getItem("upsellFromAnon")?chrome.tabs.query({active:!0,currentWindow:!0},e=>{if(e.length>0){const s=e[0].id;r.sidepanelPostAnonUpsellSignIn(!0,s,t),chrome.runtime.sendMessage({main_op:"post_upsell_anon",tabId:t})}}):chrome.runtime.sendMessage({main_op:"post_upsell",tabId:t},()=>{try{chrome.runtime.lastError&&console.error("Error sending message:",chrome.runtime.lastError.message)}finally{window.close()}}));var t;