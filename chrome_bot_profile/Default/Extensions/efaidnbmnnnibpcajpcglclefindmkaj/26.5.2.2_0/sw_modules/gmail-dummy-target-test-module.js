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
import{removeExperimentCodeForAnalytics as t,setExperimentCodeForAnalytics as e}from"../common/experimentUtils.js";import{floodgate as a}from"./floodgate.js";import{target as m}from"./target.js";const r="DCExt_Target_Dummy_Test_Build";async function o(o){if(!await a.hasFlag("dc-cv-gmail-dummy-target-test"))return void o({enableGmailDummyTargetTest:!1});const i=((t,e)=>(t||[]).find(t=>Object.prototype.hasOwnProperty.call(t,e))?.[e])(await m.getTargetOffer([r]),r);!0===i?.enable?(t("GDTC"),e("GDT")):(t("GDTC"),t("GDT")),o({enableGmailDummyTargetTest:!0===i?.enable})}export{o as gmailDummyTargetTestInit};