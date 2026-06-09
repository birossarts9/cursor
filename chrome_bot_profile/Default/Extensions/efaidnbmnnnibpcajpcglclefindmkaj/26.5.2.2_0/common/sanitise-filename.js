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
const e=/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i;export const buildSafeFilename=(t,l,c="webpage")=>{let s=(t||"").toString().replace(/[\x00-\x1F\x7F<>:"/\\|?*]/g,"").replace(/\s+/g," ").trim().replace(/[.\s]+$/,"");const n=s.split(".")[0];n&&e.test(n)&&(s=`_${s}`);const r=Math.max(1,240-l.length);if(s.length>r){let e=r;const t=s.charCodeAt(e-1);t>=55296&&t<=56319&&(e-=1),s=s.slice(0,e).replace(/[.\s]+$/,"")}return s||(s=c),s+l};export const HTML_FILE_EXTENSION=".html";