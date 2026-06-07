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
import hermesUtils from"./hermes-utils.js";class IframeManager{constructor(){this.iframeTrackingMap=new Map}getIframeInfoBySender(e){return hermesUtils.consoleLog("iframeTrackingMap.entries().length",this.iframeTrackingMap.entries().length),Array.from(this.iframeTrackingMap.entries()).find(([,r])=>{hermesUtils.consoleLog("iframeInfo",r),hermesUtils.consoleLog("sender",e),hermesUtils.consoleLog("iframeInfo.iframe.contentWindow",r.iframe.contentWindow);const n=e===r.iframe.contentWindow;return hermesUtils.consoleLog("sender === iframeInfo.iframe.contentWindow",n),r.iframe&&r.iframe.contentWindow===e})}getIframeTrackingMap(){return this.iframeTrackingMap}}const iframeManager=new IframeManager;export default iframeManager;