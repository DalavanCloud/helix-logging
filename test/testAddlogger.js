/*
 * Copyright 2018 Adobe. All rights reserved.
 * This file is licensed to you under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License. You may obtain a copy
 * of the License at http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software distributed under
 * the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR REPRESENTATIONS
 * OF ANY KIND, either express or implied. See the License for the specific language
 * governing permissions and limitations under the License.
 */
/* eslint-env mocha */
const assert = require('assert');
const addlogger = require('../src/addlogger');
const condit = require('./condit');

describe('Test addlogger', () => {
  condit('Test successful logger setup', condit.hasenvs(['CLIENT_EMAIL', 'PRIVATE_KEY', 'HLX_FASTLY_NAMESPACE', 'HLX_FASTLY_AUTH', 'PROJECT_ID', 'VERSION_NUM']), async () => {
    const res = await addlogger(
      {
        email: process.env.CLIENT_EMAIL,
        key: process.env.PRIVATE_KEY.replace(/\\n/g, '\n'),
        service: process.env.HLX_FASTLY_NAMESPACE,
        token: process.env.HLX_FASTLY_AUTH,
        project: process.env.PROJECT_ID,
        version: Number.parseInt(process.env.VERSION_NUM, 10),
      },

    );
    assert.ok(res);
    assert.equal();
  }).timeout(60000);

  condit('Test unsuccessful logger setup', condit.hasenvs(['CLIENT_EMAIL', 'PRIVATE_KEY', 'HLX_FASTLY_NAMESPACE', 'HLX_FASTLY_AUTH', 'PROJECT_ID', 'VERSION_NUM']), async () => {
    try {
      const logger = await addlogger({
        email: 'invalid@foo',
        key: process.env.PRIVATE_KEY.replace(/\\n/g, '\n'),
        service: process.env.HLX_FASTLY_NAMESPACE,
        token: process.env.HLX_FASTLY_AUTH,
        project: process.env.PROJECT_ID,
        version: Number.parseInt(process.env.VERSION_NUM, 10),
      });
      assert.fail(`${logger} should be undefined`);
    } catch (e) {
      assert.ok(e);
    }
  }).timeout(60000);
});
