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
export const sendAnalyticsEvent=e=>{try{const o=window?.location?.hostname?.toLowerCase()||"",n="chatgpt.com"===o||o.endsWith(".chatgpt.com"),t=e.map(([e,o={},...t])=>[e,{...o,isChatGPTDomain:n},...t]);chrome.runtime.sendMessage({main_op:"analytics",analytics:t})}catch(e){console.error("[FAB Utils] Analytics event error:",e)}};export const shouldDisableTouchpoints=async()=>"undefined"!=typeof GenAIWebpageEligibilityService&&await GenAIWebpageEligibilityService.shouldDisableTouchpoints();export const openSidePanel=e=>{sendAnalyticsEvent([[`DCBrowserExt:SidePanel:${e}:Clicked`]]),chrome.runtime.sendMessage({type:"open_side_panel",touchpoint:e})};export const openQnAPanel=e=>{window.initialQuestion=void 0,openSidePanel(e)};export const openSummarizeWebpage=()=>{window.initialQuestion="Give me a summary of this webpage",openSidePanel("FABPill:Summarize")};import{buildSafeFilename,HTML_FILE_EXTENSION}from"../../../../common/sanitise-filename.js";export const convertWebpageToPDF=async()=>{sendAnalyticsEvent([["DCBrowserExt:SidePanel:FABPill:ConvertToPDF:Clicked"]]);try{const{tabId:e}=await chrome.runtime.sendMessage({main_op:"get-tab-id"});if(e){const o=buildSafeFilename(document.title,HTML_FILE_EXTENSION),n=window.location.href,t=`https://convert-pdf-webpage/?tabId=${e}&tabOriginalUrl=${encodeURIComponent(n)}`,i=t+"&acrobatPromotionSource="+"webpage_chrome-convert_to_pdf";let a=`${chrome.runtime.getURL("viewer.html")}?pdfurl=${encodeURIComponent(i)}`;a=a+"&pdffilename="+encodeURIComponent(o),a=a+"&acrobatPromotionWorkflow="+encodeURIComponent("html-to-pdf"),window.open(a,"_blank")}else console.error("Failed to get tabId (undefined)")}catch(e){console.error("Failed to convert webpage to PDF",e)}};