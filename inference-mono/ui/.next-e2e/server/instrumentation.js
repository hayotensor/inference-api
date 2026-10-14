"use strict";
/*
 * ATTENTION: An "eval-source-map" devtool has been used.
 * This devtool is neither made for production nor for readable output files.
 * It uses "eval()" calls to create a separate source file with attached SourceMaps in the browser devtools.
 * If you are trying to read the output file, select a different devtool (https://webpack.js.org/configuration/devtool/)
 * or disable the default devtool with "devtool: false".
 * If you are looking for production-ready output files, see mode: "production" (https://webpack.js.org/configuration/mode/).
 */
(() => {
var exports = {};
exports.id = "instrumentation";
exports.ids = ["instrumentation"];
exports.modules = {

/***/ "node:fs":
/*!**************************!*\
  !*** external "node:fs" ***!
  \**************************/
/***/ ((module) => {

module.exports = require("node:fs");

/***/ }),

/***/ "node:path":
/*!****************************!*\
  !*** external "node:path" ***!
  \****************************/
/***/ ((module) => {

module.exports = require("node:path");

/***/ }),

/***/ "(instrument)/./instrumentation.ts":
/*!****************************!*\
  !*** ./instrumentation.ts ***!
  \****************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

eval("__webpack_require__.r(__webpack_exports__);\n/* harmony export */ __webpack_require__.d(__webpack_exports__, {\n/* harmony export */   register: () => (/* binding */ register)\n/* harmony export */ });\n/* harmony import */ var _lib_demo_mode__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! @/lib/demo-mode */ \"(instrument)/./lib/demo-mode.ts\");\n\n// HYP-58: start the status-history sampler when the server boots, so uptime\n// is recorded even when nobody has the /status page open.\nasync function register() {\n    if (_lib_demo_mode__WEBPACK_IMPORTED_MODULE_0__.UI_DEMO_MODE) return;\n    if (true) {\n        const { ensureSampler } = await __webpack_require__.e(/*! import() */ \"_instrument_lib_server_status-history_ts\").then(__webpack_require__.bind(__webpack_require__, /*! @/lib/server/status-history */ \"(instrument)/./lib/server/status-history.ts\"));\n        await ensureSampler();\n    }\n}\n//# sourceURL=[module]\n//# sourceMappingURL=data:application/json;charset=utf-8;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiKGluc3RydW1lbnQpLy4vaW5zdHJ1bWVudGF0aW9uLnRzIiwibWFwcGluZ3MiOiI7Ozs7O0FBQStDO0FBRS9DLDRFQUE0RTtBQUM1RSwwREFBMEQ7QUFDbkQsZUFBZUM7SUFDcEIsSUFBSUQsd0RBQVlBLEVBQUU7SUFDbEIsSUFBSUUsSUFBcUMsRUFBRTtRQUN6QyxNQUFNLEVBQUVHLGFBQWEsRUFBRSxHQUFHLE1BQU0sdU5BQXFDO1FBQ3JFLE1BQU1BO0lBQ1I7QUFDRiIsInNvdXJjZXMiOlsid2VicGFjazovL2luZmVyZW5jZS1hcGktdWkvLi9pbnN0cnVtZW50YXRpb24udHM/ZDdkNyJdLCJzb3VyY2VzQ29udGVudCI6WyJpbXBvcnQgeyBVSV9ERU1PX01PREUgfSBmcm9tIFwiQC9saWIvZGVtby1tb2RlXCI7XG5cbi8vIEhZUC01ODogc3RhcnQgdGhlIHN0YXR1cy1oaXN0b3J5IHNhbXBsZXIgd2hlbiB0aGUgc2VydmVyIGJvb3RzLCBzbyB1cHRpbWVcbi8vIGlzIHJlY29yZGVkIGV2ZW4gd2hlbiBub2JvZHkgaGFzIHRoZSAvc3RhdHVzIHBhZ2Ugb3Blbi5cbmV4cG9ydCBhc3luYyBmdW5jdGlvbiByZWdpc3RlcigpIHtcbiAgaWYgKFVJX0RFTU9fTU9ERSkgcmV0dXJuO1xuICBpZiAocHJvY2Vzcy5lbnYuTkVYVF9SVU5USU1FID09PSBcIm5vZGVqc1wiKSB7XG4gICAgY29uc3QgeyBlbnN1cmVTYW1wbGVyIH0gPSBhd2FpdCBpbXBvcnQoXCJAL2xpYi9zZXJ2ZXIvc3RhdHVzLWhpc3RvcnlcIik7XG4gICAgYXdhaXQgZW5zdXJlU2FtcGxlcigpO1xuICB9XG59XG4iXSwibmFtZXMiOlsiVUlfREVNT19NT0RFIiwicmVnaXN0ZXIiLCJwcm9jZXNzIiwiZW52IiwiTkVYVF9SVU5USU1FIiwiZW5zdXJlU2FtcGxlciJdLCJzb3VyY2VSb290IjoiIn0=\n//# sourceURL=webpack-internal:///(instrument)/./instrumentation.ts\n");

/***/ }),

/***/ "(instrument)/./lib/demo-mode.ts":
/*!**************************!*\
  !*** ./lib/demo-mode.ts ***!
  \**************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

eval("__webpack_require__.r(__webpack_exports__);\n/* harmony export */ __webpack_require__.d(__webpack_exports__, {\n/* harmony export */   DEMO_EMAIL: () => (/* binding */ DEMO_EMAIL),\n/* harmony export */   DEMO_PASSWORD: () => (/* binding */ DEMO_PASSWORD),\n/* harmony export */   UI_DEMO_MODE: () => (/* binding */ UI_DEMO_MODE)\n/* harmony export */ });\n// A frontend fixture mode, never a backend authentication bypass.\nconst UI_DEMO_MODE =  true && \"false\" === \"true\";\nconst DEMO_EMAIL = \"dev@example.com\";\nconst DEMO_PASSWORD = \"local-demo\";\n//# sourceURL=[module]\n//# sourceMappingURL=data:application/json;charset=utf-8;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiKGluc3RydW1lbnQpLy4vbGliL2RlbW8tbW9kZS50cyIsIm1hcHBpbmdzIjoiOzs7Ozs7QUFBQSxrRUFBa0U7QUFDM0QsTUFBTUEsZUFDWEMsS0FBc0MsSUFBSUEsT0FBK0IsS0FBSyxPQUFPO0FBRWhGLE1BQU1HLGFBQWEsa0JBQWtCO0FBQ3JDLE1BQU1DLGdCQUFnQixhQUFhIiwic291cmNlcyI6WyJ3ZWJwYWNrOi8vaW5mZXJlbmNlLWFwaS11aS8uL2xpYi9kZW1vLW1vZGUudHM/NmJiYyJdLCJzb3VyY2VzQ29udGVudCI6WyIvLyBBIGZyb250ZW5kIGZpeHR1cmUgbW9kZSwgbmV2ZXIgYSBiYWNrZW5kIGF1dGhlbnRpY2F0aW9uIGJ5cGFzcy5cbmV4cG9ydCBjb25zdCBVSV9ERU1PX01PREUgPVxuICBwcm9jZXNzLmVudi5OT0RFX0VOViA9PT0gXCJkZXZlbG9wbWVudFwiICYmIHByb2Nlc3MuZW52Lk5FWFRfUFVCTElDX1VJX0RFTU8gPT09IFwidHJ1ZVwiO1xuXG5leHBvcnQgY29uc3QgREVNT19FTUFJTCA9IFwiZGV2QGV4YW1wbGUuY29tXCI7XG5leHBvcnQgY29uc3QgREVNT19QQVNTV09SRCA9IFwibG9jYWwtZGVtb1wiO1xuIl0sIm5hbWVzIjpbIlVJX0RFTU9fTU9ERSIsInByb2Nlc3MiLCJlbnYiLCJORVhUX1BVQkxJQ19VSV9ERU1PIiwiREVNT19FTUFJTCIsIkRFTU9fUEFTU1dPUkQiXSwic291cmNlUm9vdCI6IiJ9\n//# sourceURL=webpack-internal:///(instrument)/./lib/demo-mode.ts\n");

/***/ })

};
;

// load runtime
var __webpack_require__ = require("./webpack-runtime.js");
__webpack_require__.C(exports);
var __webpack_exec__ = (moduleId) => (__webpack_require__(__webpack_require__.s = moduleId))
var __webpack_exports__ = (__webpack_exec__("(instrument)/./instrumentation.ts"));
module.exports = __webpack_exports__;

})();