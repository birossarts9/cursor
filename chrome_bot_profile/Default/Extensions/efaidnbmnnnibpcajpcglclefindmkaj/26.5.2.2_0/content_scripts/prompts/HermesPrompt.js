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
class HermesPrompt{constructor(e){this.id=e,this.timeout=3e3,this.ready=this.loadDependencies()}async loadDependencies(){const[e,s,t]=await Promise.all([import(chrome.runtime.getURL("./content_scripts/hermes/hermes-utils.js")),import(chrome.runtime.getURL("./content_scripts/hermes/message-helpers.js")),import(chrome.runtime.getURL("./content_scripts/hermes/request-callback-manager.js"))]);this.reUtils=e.default,this.messageHelpers=s.default,this.requestCallbackManager=t.default}async isEligible(){if("undefined"==typeof hermesBridgeLive)return!1;const[e,s]=await Promise.all([hermesBridgeLive.promise,this.ready]);if(!e)return!1;const t=this.reUtils.generateRequestId();return new Promise((e,s)=>{this.requestCallbackManager.registerCallback(t,s=>{e(s.payload?.result??!1)},!0),this.messageHelpers.sendIsPromptEligibleMessage(t,this.id)})}async render(){const e=this.reUtils.generateRequestId();this.messageHelpers.sendRenderPromptMessage(e,this.id)}}