// runtime can't be in strict mode because a global variable is assign and maybe created.
/*
 * ATTENTION: An "eval-source-map" devtool has been used.
 * This devtool is neither made for production nor for readable output files.
 * It uses "eval()" calls to create a separate source file with attached SourceMaps in the browser devtools.
 * If you are trying to read the output file, select a different devtool (https://webpack.js.org/configuration/devtool/)
 * or disable the default devtool with "devtool: false".
 * If you are looking for production-ready output files, see mode: "production" (https://webpack.js.org/configuration/mode/).
 */
(self["webpackChunk_N_E"] = self["webpackChunk_N_E"] || []).push([["instrumentation"],{

/***/ "(instrument)/./instrumentation.ts":
/*!****************************!*\
  !*** ./instrumentation.ts ***!
  \****************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

"use strict";
eval("__webpack_require__.r(__webpack_exports__);\n/* harmony export */ __webpack_require__.d(__webpack_exports__, {\n/* harmony export */   register: () => (/* binding */ register)\n/* harmony export */ });\n/* harmony import */ var _lib_demo_mode__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! @/lib/demo-mode */ \"(instrument)/./lib/demo-mode.ts\");\n\n// HYP-58: start the status-history sampler when the server boots, so uptime\n// is recorded even when nobody has the /status page open.\nasync function register() {\n    if (_lib_demo_mode__WEBPACK_IMPORTED_MODULE_0__.UI_DEMO_MODE) return;\n    if (false) {}\n}\n//# sourceURL=[module]\n//# sourceMappingURL=data:application/json;charset=utf-8;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiKGluc3RydW1lbnQpLy4vaW5zdHJ1bWVudGF0aW9uLnRzIiwibWFwcGluZ3MiOiI7Ozs7O0FBQStDO0FBRS9DLDRFQUE0RTtBQUM1RSwwREFBMEQ7QUFDbkQsZUFBZUM7SUFDcEIsSUFBSUQsd0RBQVlBLEVBQUU7SUFDbEIsSUFBSUUsS0FBcUMsRUFBRSxFQUcxQztBQUNIIiwic291cmNlcyI6WyJ3ZWJwYWNrOi8vX05fRS8uL2luc3RydW1lbnRhdGlvbi50cz9kN2Q3Il0sInNvdXJjZXNDb250ZW50IjpbImltcG9ydCB7IFVJX0RFTU9fTU9ERSB9IGZyb20gXCJAL2xpYi9kZW1vLW1vZGVcIjtcblxuLy8gSFlQLTU4OiBzdGFydCB0aGUgc3RhdHVzLWhpc3Rvcnkgc2FtcGxlciB3aGVuIHRoZSBzZXJ2ZXIgYm9vdHMsIHNvIHVwdGltZVxuLy8gaXMgcmVjb3JkZWQgZXZlbiB3aGVuIG5vYm9keSBoYXMgdGhlIC9zdGF0dXMgcGFnZSBvcGVuLlxuZXhwb3J0IGFzeW5jIGZ1bmN0aW9uIHJlZ2lzdGVyKCkge1xuICBpZiAoVUlfREVNT19NT0RFKSByZXR1cm47XG4gIGlmIChwcm9jZXNzLmVudi5ORVhUX1JVTlRJTUUgPT09IFwibm9kZWpzXCIpIHtcbiAgICBjb25zdCB7IGVuc3VyZVNhbXBsZXIgfSA9IGF3YWl0IGltcG9ydChcIkAvbGliL3NlcnZlci9zdGF0dXMtaGlzdG9yeVwiKTtcbiAgICBhd2FpdCBlbnN1cmVTYW1wbGVyKCk7XG4gIH1cbn1cbiJdLCJuYW1lcyI6WyJVSV9ERU1PX01PREUiLCJyZWdpc3RlciIsInByb2Nlc3MiLCJlbnYiLCJORVhUX1JVTlRJTUUiLCJlbnN1cmVTYW1wbGVyIl0sInNvdXJjZVJvb3QiOiIifQ==\n//# sourceURL=webpack-internal:///(instrument)/./instrumentation.ts\n");

/***/ }),

/***/ "(instrument)/./lib/demo-mode.ts":
/*!**************************!*\
  !*** ./lib/demo-mode.ts ***!
  \**************************/
/***/ ((__unused_webpack_module, __webpack_exports__, __webpack_require__) => {

"use strict";
eval("__webpack_require__.r(__webpack_exports__);\n/* harmony export */ __webpack_require__.d(__webpack_exports__, {\n/* harmony export */   DEMO_EMAIL: () => (/* binding */ DEMO_EMAIL),\n/* harmony export */   DEMO_PASSWORD: () => (/* binding */ DEMO_PASSWORD),\n/* harmony export */   UI_DEMO_MODE: () => (/* binding */ UI_DEMO_MODE)\n/* harmony export */ });\n// A frontend fixture mode, never a backend authentication bypass.\nconst UI_DEMO_MODE =  true && \"false\" === \"true\";\nconst DEMO_EMAIL = \"dev@example.com\";\nconst DEMO_PASSWORD = \"local-demo\";\n//# sourceURL=[module]\n//# sourceMappingURL=data:application/json;charset=utf-8;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiKGluc3RydW1lbnQpLy4vbGliL2RlbW8tbW9kZS50cyIsIm1hcHBpbmdzIjoiOzs7Ozs7QUFBQSxrRUFBa0U7QUFDM0QsTUFBTUEsZUFDWEMsS0FBc0MsSUFBSUEsT0FBK0IsS0FBSyxPQUFPO0FBRWhGLE1BQU1HLGFBQWEsa0JBQWtCO0FBQ3JDLE1BQU1DLGdCQUFnQixhQUFhIiwic291cmNlcyI6WyJ3ZWJwYWNrOi8vX05fRS8uL2xpYi9kZW1vLW1vZGUudHM/NmJiYyJdLCJzb3VyY2VzQ29udGVudCI6WyIvLyBBIGZyb250ZW5kIGZpeHR1cmUgbW9kZSwgbmV2ZXIgYSBiYWNrZW5kIGF1dGhlbnRpY2F0aW9uIGJ5cGFzcy5cbmV4cG9ydCBjb25zdCBVSV9ERU1PX01PREUgPVxuICBwcm9jZXNzLmVudi5OT0RFX0VOViA9PT0gXCJkZXZlbG9wbWVudFwiICYmIHByb2Nlc3MuZW52Lk5FWFRfUFVCTElDX1VJX0RFTU8gPT09IFwidHJ1ZVwiO1xuXG5leHBvcnQgY29uc3QgREVNT19FTUFJTCA9IFwiZGV2QGV4YW1wbGUuY29tXCI7XG5leHBvcnQgY29uc3QgREVNT19QQVNTV09SRCA9IFwibG9jYWwtZGVtb1wiO1xuIl0sIm5hbWVzIjpbIlVJX0RFTU9fTU9ERSIsInByb2Nlc3MiLCJlbnYiLCJORVhUX1BVQkxJQ19VSV9ERU1PIiwiREVNT19FTUFJTCIsIkRFTU9fUEFTU1dPUkQiXSwic291cmNlUm9vdCI6IiJ9\n//# sourceURL=webpack-internal:///(instrument)/./lib/demo-mode.ts\n");

/***/ })

},
/******/ __webpack_require__ => { // webpackRuntimeModules
/******/ var __webpack_exec__ = (moduleId) => (__webpack_require__(__webpack_require__.s = moduleId))
/******/ var __webpack_exports__ = (__webpack_exec__("(instrument)/./instrumentation.ts"));
/******/ (_ENTRIES = typeof _ENTRIES === "undefined" ? {} : _ENTRIES).middleware_instrumentation = __webpack_exports__;
/******/ }
]);