#import <Cocoa/Cocoa.h>

int main(void) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSURL *resources = NSBundle.mainBundle.resourceURL;
        NSTask *task = [NSTask new];
        task.executableURL = [resources URLByAppendingPathComponent:@"runtime/bin/python3"];
        task.arguments = @[@"-EsB", [[resources URLByAppendingPathComponent:@"desktop.py"] path]];
        NSPipe *errors = [NSPipe pipe];
        task.standardError = errors;
        NSError *failure = nil;
        if ([task launchAndReturnError:&failure]) {
            NSData *data = [errors.fileHandleForReading readDataToEndOfFile];
            [task waitUntilExit];
            if (task.terminationStatus == 0) {
                return [NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:@"http://127.0.0.1:18770/"]] ? 0 : 1;
            }
            NSString *message = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
            failure = [NSError errorWithDomain:@"Echo" code:task.terminationStatus
                                      userInfo:@{NSLocalizedDescriptionKey:message ?: @"后台服务启动失败"}];
        }
        [NSApp activateIgnoringOtherApps:YES];
        [[NSAlert alertWithError:failure] runModal];
        return 1;
    }
}
